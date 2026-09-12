package tech.datafying.localflow

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings as SysSettings
import android.text.Editable
import android.text.TextWatcher
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import java.util.concurrent.Executors

/** Setup screen: PC connection, permission checklist, the dot switch and a few preferences. */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val REQ_MIC = 10
        private const val REQ_NOTIF = 11
        private const val HIGHLIGHT_MS = 2500L

        /** Boolean extra: scroll to and highlight the PC-connection card (the dot sends this when nothing is set up). */
        const val EXTRA_SHOW_CONNECTION = "tech.datafying.localflow.extra.SHOW_CONNECTION"
    }

    private lateinit var settings: Settings
    private val worker = Executors.newSingleThreadExecutor()

    /** The user flipped "Show the dot" while the microphone was still ungranted; start it once it is. */
    private var dotStartPending = false

    private lateinit var serverUrl: EditText
    private lateinit var token: EditText
    private lateinit var urlHint: TextView
    private lateinit var connectionCard: View
    private lateinit var connectionStatus: TextView
    private lateinit var showDot: SwitchCompat
    private lateinit var showOnlyNote: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        settings = Settings(this)

        serverUrl = findViewById(R.id.serverUrl)
        token = findViewById(R.id.token)
        connectionStatus = findViewById(R.id.connectionStatus)
        urlHint = findViewById(R.id.urlHint)
        connectionCard = findViewById(R.id.connectionCard)
        showDot = findViewById(R.id.showDot)
        showOnlyNote = findViewById(R.id.showOnlyNote)

        serverUrl.setText(settings.serverUrl)
        token.setText(settings.token)
        updateUrlHint(settings.serverUrl)
        serverUrl.addTextChangedListener(afterChange {
            settings.serverUrl = it
            updateUrlHint(it)
        })
        token.addTextChangedListener(afterChange { settings.token = it })

        findViewById<Button>(R.id.pasteSetup).setOnClickListener { pasteSetupLine() }
        findViewById<Button>(R.id.testConnection).setOnClickListener { testConnection() }

        findViewById<Button>(R.id.permMicBtn).setOnClickListener { requestMic() }
        findViewById<Button>(R.id.permOverlayBtn).setOnClickListener {
            open(Intent(SysSettings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
        findViewById<Button>(R.id.permAccessBtn).setOnClickListener { openAccessibilitySettings() }
        findViewById<Button>(R.id.permNotifBtn).setOnClickListener { requestNotifications() }
        findViewById<Button>(R.id.permBatteryBtn).setOnClickListener {
            open(Intent(SysSettings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        }

        showDot.setOnCheckedChangeListener { _, on -> onShowDotToggled(on) }
        // OverlayService listens to the preference and re-evaluates the dot at once.
        bindSwitch(R.id.showOnlyWhenTyping, settings.showOnlyWhenTyping) { settings.showOnlyWhenTyping = it }
        bindSwitch(R.id.pressEnter, settings.pressEnter) { settings.pressEnter = it }
        bindSwitch(R.id.haptics, settings.haptics) { settings.haptics = it }
        bindSwitch(R.id.sounds, settings.sounds) { settings.sounds = it }

        findViewById<TextView>(R.id.versionText).text =
            "LocalFlow for Android ${BuildConfig.VERSION_NAME} · API contract v1"

        if (intent?.getBooleanExtra(EXTRA_SHOW_CONNECTION, false) == true) highlightConnection()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.getBooleanExtra(EXTRA_SHOW_CONNECTION, false)) highlightConnection()
    }

    /** Scroll the connection card into view, flash it, and put the caret in the empty field. */
    private fun highlightConnection() {
        connectionCard.post {
            var p = connectionCard.parent
            while (p != null && p !is ScrollView) p = p.parent
            (p as? ScrollView)?.smoothScrollTo(0, connectionCard.top)
            val flash = (ContextCompat.getColor(this, R.color.dot_processing) and 0x00FFFFFF) or 0x33000000
            connectionCard.setBackgroundColor(flash)
            connectionCard.postDelayed({ connectionCard.background = null }, HIGHLIGHT_MS)
            if (serverUrl.text.isNullOrBlank()) serverUrl.requestFocus() else if (token.text.isNullOrBlank()) token.requestFocus()
        }
    }

    private fun updateUrlHint(url: String) {
        urlHint.visibility = if (url.isBlank()) View.VISIBLE else View.GONE
    }

    override fun onResume() {
        super.onResume()
        refreshPermissions()
        // The mic prompt was answered on the system page (after two denials Android sends the
        // user there instead of showing a dialog): pick the pending switch up here.
        if (dotStartPending && micGranted()) {
            dotStartPending = false
            settings.showDot = true
        }
        // Keep the switch honest and (re)start the dot once the overlay permission arrives.
        val canShow = settings.showDot && SysSettings.canDrawOverlays(this)
        if (canShow && !OverlayService.isRunning) OverlayService.start(this)
        setDotSwitch(canShow)
    }

    /** Sets the switch without firing [onShowDotToggled]. */
    private fun setDotSwitch(on: Boolean) {
        showDot.setOnCheckedChangeListener(null)
        showDot.isChecked = on
        showDot.setOnCheckedChangeListener { _, checked -> onShowDotToggled(checked) }
    }

    private fun micGranted(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    override fun onDestroy() {
        worker.shutdownNow()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ connection
    private fun pasteSetupLine() {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString()
        val parsed = Settings.parseSetupLine(text)
        if (parsed == null) {
            toast("Clipboard does not contain a URL|TOKEN line. On the PC: tray icon → Phone setup → Copy.")
            return
        }
        serverUrl.setText(parsed.first)
        token.setText(parsed.second)
        // Echo the host so the user can see the right PC was picked up; never the token.
        val host = Uri.parse(parsed.first).host ?: parsed.first
        toast("Setup line pasted: PC is $host. Testing the connection…")
        testConnection()
    }

    private fun testConnection() {
        settings.serverUrl = serverUrl.text.toString()
        settings.token = token.text.toString()
        connectionStatus.text = "Testing ${settings.serverUrl} …"
        val api = ApiClient(settings)
        worker.execute {
            val msg = try {
                val h = api.health()
                buildString {
                    append("Connected: LocalFlow ").append(h.version)
                    append(" · ").append(h.engine)
                    append(if (h.gpu) " on GPU" else " on CPU")
                    if (h.llm.isNotBlank()) append(" · ").append(h.llm).append(if (h.llmOk) "" else " (LLM down)")
                    append(if (h.ready) " · ready" else " · PC is warming up, try again in a minute")
                    if (settings.token.isBlank()) append("\nNo token yet: dictation will get 401 until you paste one.")
                }
            } catch (e: ApiClient.ApiException) {
                when (e.kind) {
                    ApiClient.Kind.NETWORK -> "PC not reachable. Is Tailscale on (phone and PC), and is phone access enabled in the PC tray?"
                    ApiClient.Kind.BAD_URL -> e.message ?: ApiClient.MSG_URL_MALFORMED   // blank vs malformed
                    else -> "Error: ${e.message}"
                }
            } catch (e: Exception) {
                "Error: ${e.message}"
            }
            runOnUiThread { if (!isFinishing) connectionStatus.text = msg }
        }
    }

    // ------------------------------------------------------------------ permissions
    private fun refreshPermissions() {
        val mic = micGranted()
        val overlay = SysSettings.canDrawOverlays(this)
        val access = InsertionService.isEnabled(this)
        val notif = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        } else {
            NotificationManagerCompat.from(this).areNotificationsEnabled()
        }
        val battery = (getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(packageName)

        setPerm(R.id.permMicText, R.id.permMicBtn, mic, "Microphone", "records your voice")
        setPerm(R.id.permOverlayText, R.id.permOverlayBtn, overlay, "Display over other apps", "shows the dot")
        setPerm(R.id.permAccessText, R.id.permAccessBtn, access, "Accessibility service", "types the text into the focused field")
        setPerm(R.id.permNotifText, R.id.permNotifBtn, notif, "Notifications", "keeps the dot alive in the background")
        setPerm(R.id.permBatteryText, R.id.permBatteryBtn, battery, "Battery: unrestricted", "stops Android killing the dot")

        // Without the accessibility service the app cannot tell when typing is possible.
        showOnlyNote.visibility = if (access) View.GONE else View.VISIBLE
    }

    private fun setPerm(textId: Int, btnId: Int, ok: Boolean, name: String, why: String) {
        findViewById<TextView>(textId).text = (if (ok) "✅ " else "⬜ ") + name + "\n" + why
        findViewById<Button>(btnId).apply {
            text = if (ok) "Done" else "Open"
            isEnabled = !ok
        }
    }

    private fun requestMic() {
        if (ActivityCompat.shouldShowRequestPermissionRationale(this, Manifest.permission.RECORD_AUDIO) ||
            !settings.micAsked
        ) {
            settings.micAsked = true
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
        } else {
            // Denied twice: only the system page can flip it now.
            openAppDetails()
        }
    }

    private fun requestNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIF)
        } else {
            open(Intent(SysSettings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(SysSettings.EXTRA_APP_PACKAGE, packageName))
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        refreshPermissions()
        if (requestCode == REQ_NOTIF && grantResults.firstOrNull() == PackageManager.PERMISSION_DENIED) {
            open(Intent(SysSettings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(SysSettings.EXTRA_APP_PACKAGE, packageName))
        }
        if (requestCode == REQ_MIC && dotStartPending) {
            dotStartPending = false
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
                // Now the service can start straight away as a MICROPHONE foreground service.
                settings.showDot = true
                setDotSwitch(true)
                OverlayService.start(this)
            } else {
                settings.showDot = false
                setDotSwitch(false)
                toast("The dot needs the microphone to record. Allow it (Permissions → Microphone), then switch the dot on again.")
            }
        }
    }

    private fun openAccessibilitySettings() {
        // Some OEM builds honour the fragment-args hint and land on our service directly;
        // everyone else gets the Accessibility list, where LocalFlow is under Installed apps.
        val component = "$packageName/${InsertionService::class.java.name}"
        val intent = Intent(SysSettings.ACTION_ACCESSIBILITY_SETTINGS)
        val args = Bundle().apply { putString(":settings:fragment_args_key", component) }
        intent.putExtra(":settings:fragment_args_key", component)
        intent.putExtra(":settings:show_fragment_args", args)
        toast("Find LocalFlow under Installed apps and switch it on")
        open(intent)
    }

    private fun openAppDetails() {
        open(Intent(SysSettings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
    }

    private fun open(intent: Intent) {
        try {
            startActivity(intent)
        } catch (e: Exception) {
            toast("This phone has no page for that; look for it under Settings → Apps → LocalFlow")
        }
    }

    // ------------------------------------------------------------------ dot + switches
    private fun onShowDotToggled(on: Boolean) {
        if (on) {
            if (!SysSettings.canDrawOverlays(this)) {
                toast("Allow “Display over other apps” first")
                showDot.isChecked = false
                open(Intent(SysSettings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
                return
            }
            if (!micGranted()) {
                // Android 14+ refuses a microphone foreground service until RECORD_AUDIO is
                // granted, so ask first and start from onRequestPermissionsResult / onResume.
                dotStartPending = true
                setDotSwitch(false)
                toast("Allow the microphone first; the dot starts as soon as it is granted")
                requestMic()
                return
            }
            settings.showDot = true
            OverlayService.start(this)
        } else {
            settings.showDot = false
            OverlayService.stop(this)
        }
    }

    private fun bindSwitch(id: Int, initial: Boolean, onChange: (Boolean) -> Unit) {
        findViewById<SwitchCompat>(id).apply {
            isChecked = initial
            setOnCheckedChangeListener { _, on -> onChange(on) }
        }
    }

    private fun afterChange(fn: (String) -> Unit) = object : TextWatcher {
        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
        override fun afterTextChanged(s: Editable?) = fn(s?.toString() ?: "")
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
