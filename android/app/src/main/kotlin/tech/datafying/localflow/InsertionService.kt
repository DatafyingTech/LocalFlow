package tech.datafying.localflow

import android.accessibilityservice.AccessibilityService
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.provider.Settings as SysSettings
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo

/**
 * Types the PC's text into the focused field of whatever app is in front.
 *
 * Order of attempts (see docs/API.md, "What the phone should do with text"):
 *  1. focused editable node that supports ACTION_SET_TEXT: splice at caret / over selection,
 *     then ACTION_SET_SELECTION to put the caret after the new text;
 *  2. clipboard + ACTION_PASTE on the focused node;
 *  3. clipboard only (the overlay toasts "Copied, paste it where you want").
 */
class InsertionService : AccessibilityService() {

    enum class Outcome { INSERTED, PASTED, COPIED }

    companion object {
        private const val TAG = "LocalFlow.Insert"

        @Volatile
        var instance: InsertionService? = null
            private set

        /**
         * Last known "the user can type right now": an editable node has input focus or a keyboard
         * window is showing. Only meaningful while [instance] is non-null.
         */
        @Volatile
        var canType: Boolean = false
            private set

        /**
         * Called (on the accessibility service's thread) whenever [canType] is recomputed, and with
         * false when the service goes away. OverlayService sets this and posts to its main handler.
         */
        @Volatile
        var canTypeListener: ((Boolean) -> Unit)? = null

        /** True when the user has switched the service on in Settings > Accessibility. */
        fun isEnabled(context: Context): Boolean {
            val id = "${context.packageName}/${InsertionService::class.java.name}"
            val enabled = SysSettings.Secure.getString(
                context.contentResolver, SysSettings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            ) ?: return false
            return enabled.split(':').any { it.equals(id, ignoreCase = true) }
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        publishCanType()
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        publishCanType(false)
        super.onDestroy()
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        if (instance === this) instance = null
        publishCanType(false)
        return super.onUnbind(intent)
    }

    /**
     * Text insertion itself is on demand; events are only used to track whether the user can type
     * (which is what decides if the dot is shown). The event types come from
     * res/xml/accessibility_service_config.xml.
     */
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        when (event?.eventType) {
            AccessibilityEvent.TYPE_VIEW_FOCUSED,
            AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED,
            AccessibilityEvent.TYPE_VIEW_CLICKED,
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOWS_CHANGED -> publishCanType()
            else -> {}
        }
    }

    override fun onInterrupt() {}

    /** Recomputes [canType] (or takes [forced]) and tells the listener when it changed. */
    private fun publishCanType(forced: Boolean? = null) {
        val now = forced ?: computeCanType()
        val changed = now != canType
        canType = now
        if (changed || forced != null) {
            try { canTypeListener?.invoke(now) } catch (e: Exception) { Log.w(TAG, "canType listener", e) }
        }
    }

    private fun computeCanType(): Boolean = try {
        focusedEditable() || windows.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
    } catch (e: Exception) {
        Log.d(TAG, "computeCanType", e)
        false
    }

    private fun focusedEditable(): Boolean = findFocusedNode()?.isEditable == true

    /** Package name of the app the user is dictating into, for the API's `app` field. */
    fun focusedPackage(): String? = try {
        findFocusedNode()?.packageName?.toString() ?: rootInActiveWindow?.packageName?.toString()
    } catch (e: Exception) {
        null
    }

    /**
     * @param separate this is a hands-free result following another hands-free result.
     * @param pressEnter the server said "press enter" and the user has that switch on.
     */
    fun insert(text: String, separate: Boolean, pressEnter: Boolean): Outcome {
        val node = try { findFocusedNode() } catch (e: Exception) { null }

        if (node != null && node.isEditable && supportsSetText(node)) {
            val existing = if (isShowingHint(node)) "" else node.text?.toString() ?: ""
            val r = TextSplice.splice(existing, node.textSelectionStart, node.textSelectionEnd, text, separate)
            val args = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, r.text)
            }
            if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
                val sel = Bundle().apply {
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, r.caret)
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, r.caret)
                }
                node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, sel)
                if (pressEnter) pressEnter(node)
                return Outcome.INSERTED
            }
            Log.w(TAG, "ACTION_SET_TEXT refused by ${node.packageName}; falling back to paste")
        }

        // Clipboard route. Apply the same separator rule from what we can see of the field.
        val before = if (node != null && !isShowingHint(node)) {
            val t = node.text?.toString() ?: ""
            val cut = node.textSelectionStart.let { if (it < 0) t.length else it.coerceAtMost(t.length) }
            t.substring(0, cut)
        } else ""
        val clipText = if (TextSplice.needsSeparator(before, separate)) " $text" else text
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("LocalFlow", clipText))

        val target = node ?: try { rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) } catch (e: Exception) { null }
        if (target != null && target.performAction(AccessibilityNodeInfo.ACTION_PASTE)) {
            if (pressEnter) pressEnter(target)
            return Outcome.PASTED
        }
        return Outcome.COPIED
    }

    private fun findFocusedNode(): AccessibilityNodeInfo? {
        rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let { return it }
        // The active window can be a system dialog or our own overlay; look through app windows too.
        for (w in windows) {
            if (w.type != AccessibilityWindowInfo.TYPE_APPLICATION) continue
            w.root?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let { return it }
        }
        return null
    }

    private fun supportsSetText(node: AccessibilityNodeInfo): Boolean =
        node.actionList.any { it.id == AccessibilityNodeInfo.ACTION_SET_TEXT }

    private fun isShowingHint(node: AccessibilityNodeInfo): Boolean =
        node.isShowingHintText

    /**
     * An accessibility service cannot inject raw key events. ACTION_IME_ENTER (Android 11+) is the
     * documented equivalent of the keyboard's Enter key; older versions get a newline at the caret.
     */
    private fun pressEnter(node: AccessibilityNodeInfo) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)) return
        }
        node.refresh()
        val t = node.text?.toString() ?: ""
        val caret = node.textSelectionEnd.let { if (it < 0) t.length else it.coerceAtMost(t.length) }
        val r = TextSplice.splice(t, caret, caret, "\n", separate = false)
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, r.text) }
        if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
            val sel = Bundle().apply {
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, r.caret)
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, r.caret)
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, sel)
        }
    }
}
