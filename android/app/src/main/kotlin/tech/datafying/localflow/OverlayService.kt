package tech.datafying.localflow

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.graphics.Point
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.provider.Settings as SysSettings
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.Toast
import androidx.annotation.RequiresApi
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import java.util.concurrent.Executors
import kotlin.math.hypot

/**
 * Foreground service that owns the floating dot: window, gestures, recording, the API call and
 * handing the text to InsertionService. All state lives on the main thread.
 *
 * Gestures on the compact dot:
 *   hold      -> push-to-talk; release = send (release over [x] = discard)
 *   tap       -> hands-free on; tap the dot or [check] = send, [x] = discard
 *   drag      -> move; snaps to the nearest edge and remembers
 *   tap while the red ring is showing after a timeout -> resend the kept audio
 */
class OverlayService : Service(), View.OnTouchListener {

    companion object {
        private const val TAG = "LocalFlow.Overlay"
        const val ACTION_STOP = "tech.datafying.localflow.action.STOP"
        private const val CHANNEL_ID = "localflow_dot"
        private const val NOTIF_ID = 1
        private const val HANDSFREE_MAX_MS = 20L * 60 * 1000
        private const val MIN_AUDIO_MS = 250L
        private const val DONE_MS = 700L
        private const val ERROR_MS = 2500L
        /** Focus hops between fields (and keyboard show/hide) within this window do not blink the dot. */
        private const val HIDE_DEBOUNCE_MS = 350L
        /** Minimum gap between focus-triggered /v1/warm calls (recording start is not limited). */
        private const val FOCUS_WARM_INTERVAL_MS = 60_000L

        @Volatile
        var isRunning: Boolean = false
            private set

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, OverlayService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, OverlayService::class.java))
        }
    }

    private lateinit var settings: Settings
    private lateinit var wm: WindowManager
    private lateinit var dot: DotView
    private lateinit var lp: WindowManager.LayoutParams
    private lateinit var api: ApiClient
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "localflow-net") }

    private var recorder: Recorder? = null
    private var currentMode: String = ApiClient.MODE_PTT
    private var pendingWav: ByteArray? = null      // kept after a timeout so a tap can retry
    /** Last error toast, re-shown when the user taps the red ring to ask what happened. */
    private var lastErrorMessage: String? = null
    private var pendingMode: String = ApiClient.MODE_PTT
    private var lastResultHandsfree = false
    private var resetJob: Runnable? = null
    private var compactX = 0                        // dot x before the pill expanded
    private var toneGen: ToneGenerator? = null
    /** Foreground-service type we are currently running as (0 below Android 10). */
    private var fgType = 0

    // visibility: "show only when typing" (see DotVisibility)
    private var canType = false
    private val hideJob = Runnable { applyVisibility() }
    private val canTypeListener: (Boolean) -> Unit = { v -> main.post { onCanType(v) } }
    private val prefsListener = android.content.SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key == Settings.KEY_SHOW_ONLY_WHEN_TYPING) reevaluateVisibility()
    }

    // gesture bookkeeping
    private var downRawX = 0f
    private var downRawY = 0f
    private var startX = 0
    private var startY = 0
    private var moved = false
    private var pttFired = false
    private var touchSlop = 8f
    private val longPress = Runnable {
        pttFired = true
        startRecording(ApiClient.MODE_PTT)
    }

    private val state: DotView.State get() = dot.state

    // ------------------------------------------------------------------ lifecycle
    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        api = ApiClient(settings)
        wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        touchSlop = ViewConfiguration.get(this).scaledTouchSlop.toFloat()

        if (!startInForeground()) {
            // Not a "mic refused" case: Android would not let us be a foreground service at all
            // (started from the background on 12+, say). Staying alive would earn an ANR in ~10 s.
            stopSelf()
            return
        }
        if (!SysSettings.canDrawOverlays(this)) {
            toast("LocalFlow needs “Display over other apps” to show the dot")
            stopSelf()
            return
        }
        addDot()
        isRunning = true

        canType = InsertionService.canType
        InsertionService.canTypeListener = canTypeListener
        settings.addListener(prefsListener)
        reevaluateVisibility()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            settings.showDot = false
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onDestroy() {
        isRunning = false
        if (InsertionService.canTypeListener === canTypeListener) InsertionService.canTypeListener = null
        if (::settings.isInitialized) settings.removeListener(prefsListener)
        main.removeCallbacksAndMessages(null)
        recorder?.discard()
        recorder = null
        if (::dot.isInitialized && dot.isAttachedToWindow) {
            try { wm.removeView(dot) } catch (e: Exception) { Log.w(TAG, "removeView", e) }
        }
        worker.shutdownNow()
        toneGen?.release()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        if (::dot.isInitialized && dot.isAttachedToWindow && !dot.expanded) snapToEdge(persist = false)
    }

    private fun micGranted(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun buildNotification(): Notification {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(CHANNEL_ID, getString(R.string.notif_channel), NotificationManager.IMPORTANCE_LOW)
        channel.setShowBadge(false)
        nm.createNotificationChannel(channel)

        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, OverlayService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_dot)
            .setContentTitle(getString(R.string.notif_title))
            .setContentText(getString(R.string.notif_text))
            .setContentIntent(open)
            .addAction(0, getString(R.string.notif_stop), stop)
            .setOngoing(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    /**
     * Promote to a foreground service. Android 14+ refuses the MICROPHONE type until RECORD_AUDIO
     * is granted, so without the permission we start as SPECIAL_USE (declared in the manifest)
     * so the dot still appears and can send the user to the permission. [ensureMicType] upgrades
     * later. Returns false only when Android refused to make us a foreground service at all.
     */
    private fun startInForeground(): Boolean {
        val notif = buildNotification()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            return try {
                startForeground(NOTIF_ID, notif)
                true
            } catch (e: Exception) {
                Log.e(TAG, "startForeground refused", e)
                toast("LocalFlow could not start the dot: ${e.message}")
                false
            }
        }
        val wantMic = Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE || micGranted()
        if (wantMic && tryForeground(notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)) return true
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            if (!wantMic) Log.i(TAG, "RECORD_AUDIO not granted yet; starting as SPECIAL_USE")
            if (tryForeground(notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)) {
                if (!wantMic) toast("LocalFlow needs the microphone: grant it in the app before dictating")
                return true
            }
        }
        toast("LocalFlow could not start the dot; open the app and try the switch again")
        return false
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private fun tryForeground(notif: Notification, type: Int): Boolean = try {
        startForeground(NOTIF_ID, notif, type)
        fgType = type
        true
    } catch (e: Exception) {
        // SecurityException (permission for the type missing), ForegroundServiceStartNotAllowed...
        Log.e(TAG, "startForeground(type=$type) refused", e)
        false
    }

    /**
     * Android 14+: we may be running as SPECIAL_USE because the mic permission arrived after the
     * dot did. Re-call startForeground with the MICROPHONE type before recording; the visible
     * overlay window is one of the documented exemptions for that while-in-use upgrade.
     */
    private fun ensureMicType() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) return
        if (fgType == ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE) return
        if (!micGranted()) return
        if (!tryForeground(buildNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)) {
            // Keep going: the dot is still alive, the recording may still work, and the user
            // gets told rather than nothing happening.
            toast("Android refused microphone access for the dot; try again from the app")
        }
    }

    // ------------------------------------------------------------------ window
    @SuppressLint("ClickableViewAccessibility")
    private fun addDot() {
        dot = DotView(this)
        dot.setOnTouchListener(this)
        val size = dot.compactPx()
        val screen = screenSize()
        var x = settings.dotX
        var y = settings.dotY
        if (x < 0 || y < 0 || x > screen.x - size || y > screen.y - size) {
            x = screen.x - size
            y = (screen.y * 0.6f).toInt()
        }
        lp = WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            this.x = x
            this.y = y
        }
        wm.addView(dot, lp)
    }

    private fun screenSize(): Point {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val b = wm.currentWindowMetrics.bounds
            Point(b.width(), b.height())
        } else {
            @Suppress("DEPRECATION")
            Point().also { wm.defaultDisplay.getSize(it) }
        }
    }

    private fun updateWindow() {
        if (dot.isAttachedToWindow) {
            try { wm.updateViewLayout(dot, lp) } catch (e: Exception) { Log.w(TAG, "updateViewLayout", e) }
        }
    }

    private fun snapToEdge(persist: Boolean) {
        val screen = screenSize()
        val size = dot.compactPx()
        lp.x = if (lp.x + size / 2 < screen.x / 2) 0 else screen.x - size
        lp.y = lp.y.coerceIn(0, maxOf(0, screen.y - size))
        updateWindow()
        if (persist) {
            settings.dotX = lp.x
            settings.dotY = lp.y
        }
    }

    private fun expand() {
        if (dot.expanded) return
        compactX = lp.x
        val screen = screenSize()
        val pill = dot.pillPx()
        val size = dot.compactPx()
        // Grow towards the middle of the screen so the pill stays on-screen.
        lp.x = if (lp.x + size / 2 > screen.x / 2) lp.x + size - pill else lp.x
        lp.x = lp.x.coerceIn(0, maxOf(0, screen.x - pill))
        lp.width = pill
        dot.clearLevels()
        dot.cancelHighlight = false
        dot.expanded = true
        updateWindow()
    }

    private fun collapse() {
        if (!dot.expanded) return
        lp.x = compactX
        lp.width = dot.compactPx()
        dot.expanded = false
        dot.cancelHighlight = false
        updateWindow()
    }

    // ------------------------------------------------------------------ visibility
    private fun onCanType(v: Boolean) {
        val gainedFocus = v && !canType
        canType = v
        reevaluateVisibility()
        // A text field just gained focus: the user is likely about to dictate, so nudge the PC
        // to reload its cleanup model now (rate-limited; harmless when already loaded).
        if (gainedFocus) warmPc(force = false)
    }

    // ------------------------------------------------------------------ pre-warm
    private var lastFocusWarmMs = 0L

    /**
     * Fire-and-forget POST /v1/warm on the network thread. [force] skips the 60 s rate limit
     * (used when recording actually starts). Silent when the app is not connected yet.
     */
    private fun warmPc(force: Boolean) {
        if (settings.serverUrl.isBlank() || settings.token.isBlank()) return
        if (!force) {
            val now = SystemClock.elapsedRealtime()
            if (now - lastFocusWarmMs < FOCUS_WARM_INTERVAL_MS) return
            lastFocusWarmMs = now
        }
        if (!::api.isInitialized) return
        worker.execute { api.warm() }
    }

    private fun wantVisible(): Boolean = DotVisibility.decide(
        canType = canType,
        state = state,
        showOnlyWhenTyping = settings.showOnlyWhenTyping,
        serviceEnabled = InsertionService.instance != null,
    )

    /** Show immediately; hide after [HIDE_DEBOUNCE_MS] unless something makes it wanted again. */
    private fun reevaluateVisibility() {
        if (!::dot.isInitialized) return
        if (wantVisible()) {
            main.removeCallbacks(hideJob)
            setDotVisible(true)
        } else if (dot.visibility == View.VISIBLE) {
            main.removeCallbacks(hideJob)
            main.postDelayed(hideJob, HIDE_DEBOUNCE_MS)
        }
    }

    private fun applyVisibility() {
        if (!::dot.isInitialized) return
        setDotVisible(wantVisible())
    }

    /**
     * INVISIBLE (not removing the window) keeps the position and the layout params; the window
     * manager stops routing touches to a window whose root view is not VISIBLE.
     */
    private fun setDotVisible(visible: Boolean) {
        val target = if (visible) View.VISIBLE else View.INVISIBLE
        if (dot.visibility == target) return
        if (!visible) {
            // A finger may be resting on the dot: drop the pending long-press so it cannot start
            // a recording from a dot that is no longer on screen.
            main.removeCallbacks(longPress)
            moved = false
        }
        dot.visibility = target
    }

    // ------------------------------------------------------------------ gestures
    override fun onTouch(v: View, ev: MotionEvent): Boolean {
        if (dot.visibility != View.VISIBLE) return false   // a hidden dot never intercepts touches
        when (ev.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downRawX = ev.rawX
                downRawY = ev.rawY
                startX = lp.x
                startY = lp.y
                moved = false
                pttFired = false
                if (state == DotView.State.IDLE || state == DotView.State.ERROR || state == DotView.State.DONE) {
                    main.postDelayed(longPress, ViewConfiguration.getLongPressTimeout().toLong())
                }
            }
            MotionEvent.ACTION_MOVE -> {
                if (pttFired) {
                    dot.cancelHighlight = dot.regionAt(localX(ev)) == DotView.Region.CANCEL
                    return true
                }
                val dx = ev.rawX - downRawX
                val dy = ev.rawY - downRawY
                if (!moved && !dot.expanded && hypot(dx, dy) > touchSlop) {
                    moved = true
                    main.removeCallbacks(longPress)
                }
                if (moved) {
                    lp.x = (startX + dx).toInt()
                    lp.y = (startY + dy).toInt()
                    updateWindow()
                }
            }
            MotionEvent.ACTION_UP -> {
                main.removeCallbacks(longPress)
                when {
                    pttFired -> if (dot.regionAt(localX(ev)) == DotView.Region.CANCEL) discard() else stopAndSend()
                    moved -> snapToEdge(persist = true)
                    else -> onTap(dot.regionAt(localX(ev)))
                }
            }
            MotionEvent.ACTION_CANCEL -> {
                main.removeCallbacks(longPress)
                if (pttFired) stopAndSend() else if (moved) snapToEdge(persist = true)
            }
        }
        return true
    }

    private fun localX(ev: MotionEvent): Float {
        val loc = IntArray(2)
        dot.getLocationOnScreen(loc)
        return ev.rawX - loc[0]
    }

    private fun onTap(region: DotView.Region) {
        when (state) {
            DotView.State.IDLE -> startRecording(ApiClient.MODE_HANDSFREE)
            DotView.State.HANDSFREE -> if (region == DotView.Region.CANCEL) discard() else stopAndSend()
            DotView.State.ERROR -> {
                // The toast that explained the error is long gone by the time anyone looks at the
                // red ring, so say it again on the tap that asks about it.
                lastErrorMessage?.let { toast(it) }
                val wav = pendingWav
                if (wav != null) send(wav, pendingMode) else setState(DotView.State.IDLE)
            }
            DotView.State.LISTENING, DotView.State.PROCESSING, DotView.State.DONE -> {}
        }
    }

    // ------------------------------------------------------------------ recording
    private fun startRecording(mode: String) {
        if (recorder != null) return
        if (!micGranted()) {
            toast("LocalFlow needs the microphone. Opening settings…")
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            pttFired = false
            return
        }
        if (settings.serverUrl.isBlank() || settings.token.isBlank()) {
            // Nothing to send the audio to. Do not record; take the user to the connection card.
            toast("LocalFlow is not connected to your PC yet. Opening setup…")
            setState(DotView.State.ERROR, ERROR_MS)
            startActivity(
                Intent(this, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    .putExtra(MainActivity.EXTRA_SHOW_CONNECTION, true),
            )
            return
        }
        warmPc(force = true)   // hide the PC's model reload behind the user speaking
        ensureMicType()
        val rec = Recorder(
            maxMillis = HANDSFREE_MAX_MS,
            onLevel = { rms -> main.post { if (recorder != null) dot.pushLevel(rms) } },
            onLimit = { main.post { if (recorder != null) stopAndSend() } },
        )
        if (!rec.start()) {
            toast("Microphone is busy or unavailable")
            pttFired = false
            setState(DotView.State.ERROR, ERROR_MS)
            return
        }
        recorder = rec
        currentMode = mode
        pendingWav = null
        expand()
        setState(if (mode == ApiClient.MODE_PTT) DotView.State.LISTENING else DotView.State.HANDSFREE)
        haptic()
        tone(if (mode == ApiClient.MODE_PTT) ToneGenerator.TONE_PROP_BEEP else ToneGenerator.TONE_PROP_BEEP2)
    }

    private fun stopAndSend() {
        val rec = recorder ?: return
        recorder = null
        val pcm = rec.stop()
        collapse()
        haptic()
        if (Wav.durationSeconds(pcm.size) * 1000 < MIN_AUDIO_MS) {
            setState(DotView.State.IDLE)
            return
        }
        send(Wav.build(pcm), currentMode)
    }

    private fun discard() {
        recorder?.discard()
        recorder = null
        pendingWav = null
        collapse()
        setState(DotView.State.IDLE)
        haptic()
        tone(ToneGenerator.TONE_PROP_NACK)
    }

    // ------------------------------------------------------------------ network + insertion
    private fun send(wav: ByteArray, mode: String) {
        pendingWav = wav
        pendingMode = mode
        setState(DotView.State.PROCESSING)
        val app = InsertionService.instance?.focusedPackage()
        worker.execute {
            try {
                val result = api.dictate(wav, mode, app)
                main.post { onResult(result, mode) }
            } catch (e: ApiClient.ApiException) {
                main.post { onError(e) }
            } catch (e: Exception) {
                Log.e(TAG, "dictate failed", e)
                main.post { onError(ApiClient.ApiException(ApiClient.Kind.SERVER, e.message ?: "Unexpected error")) }
            }
        }
    }

    private fun onResult(r: ApiClient.Dictation, mode: String) {
        pendingWav = null
        lastErrorMessage = null
        if (r.empty || r.text.isEmpty()) {
            setState(DotView.State.IDLE)
            return
        }
        val handsfree = mode == ApiClient.MODE_HANDSFREE
        val separate = handsfree && lastResultHandsfree
        lastResultHandsfree = handsfree
        val pressEnter = r.pressEnter && settings.pressEnter

        val svc = InsertionService.instance
        if (svc == null) {
            copyToClipboard(r.text)
            toast("Copied, paste it where you want (turn on the LocalFlow accessibility service to auto-type)")
        } else {
            when (svc.insert(r.text, separate, pressEnter)) {
                InsertionService.Outcome.INSERTED, InsertionService.Outcome.PASTED -> {}
                InsertionService.Outcome.COPIED -> toast("Copied, paste it where you want")
            }
        }
        setState(DotView.State.DONE, DONE_MS)
        haptic()
        tone(ToneGenerator.TONE_PROP_ACK)
    }

    private fun onError(e: ApiClient.ApiException) {
        Log.w(TAG, "API error ${e.kind} (${e.causeName}): ${e.message}")
        lastErrorMessage = e.message ?: ApiClient.messageFor(e.kind)
        toast(lastErrorMessage ?: "Error")
        haptic()
        tone(ToneGenerator.TONE_PROP_NACK)
        if (e.retryable) {
            // Keep the audio; the ring stays until the user taps (retry) or holds (new recording).
            setState(DotView.State.ERROR)
        } else {
            pendingWav = null
            setState(DotView.State.ERROR, ERROR_MS)
        }
    }

    private fun copyToClipboard(text: String) {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("LocalFlow", text))
    }

    // ------------------------------------------------------------------ feedback
    private fun setState(s: DotView.State, autoResetMs: Long = 0) {
        resetJob?.let { main.removeCallbacks(it) }
        resetJob = null
        dot.state = s
        reevaluateVisibility()
        if (autoResetMs > 0) {
            val job = Runnable {
                resetJob = null
                if (dot.state == s) {
                    dot.state = DotView.State.IDLE
                    // The DONE flash / error ring is over: apply whatever visibility is pending.
                    reevaluateVisibility()
                }
            }
            resetJob = job
            main.postDelayed(job, autoResetMs)
        }
    }

    private fun haptic() {
        if (!settings.haptics) return
        try {
            val vib: Vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                (getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager).defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                vib.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK))
            } else {
                vib.vibrate(VibrationEffect.createOneShot(20, VibrationEffect.DEFAULT_AMPLITUDE))
            }
        } catch (e: Exception) {
            Log.d(TAG, "haptic", e)
        }
    }

    private fun tone(type: Int) {
        if (!settings.sounds) return
        try {
            val tg = toneGen ?: ToneGenerator(AudioManager.STREAM_NOTIFICATION, 60).also { toneGen = it }
            tg.startTone(type, 80)
        } catch (e: Exception) {
            Log.d(TAG, "tone", e)
        }
    }

    private fun toast(msg: String) {
        Toast.makeText(applicationContext, msg, Toast.LENGTH_SHORT).show()
    }
}
