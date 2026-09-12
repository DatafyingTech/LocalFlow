package tech.datafying.localflow

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.view.View
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * The floating dot. Compact: a 44 dp touch target with a 22 dp dark disc and a coloured
 * 10 dp centre, states and colours mirroring localflow/ui.py. Expanded (while recording): a
 * 200 x 44 dp pill, [x] waveform [check].
 *
 * The view only draws; gestures are interpreted by OverlayService, which asks [regionAt].
 */
class DotView(context: Context) : View(context) {

    enum class State { IDLE, LISTENING, HANDSFREE, PROCESSING, DONE, ERROR }
    enum class Region { CANCEL, CONFIRM, DOT }

    companion object {
        const val COMPACT_DP = 44f
        const val PILL_DP = 200f
        private const val DISC_DP = 22f
        private const val DOT_DP = 10f
        private const val BARS = 20
        /** Mic RMS is ~0.02-0.3 for speech; this maps that onto 0..1 for the bars. */
        private const val LEVEL_GAIN = 6f

        private val BG = Color.parseColor("#1F2937")
        private val IDLE = Color.parseColor("#6B7280")
        private val LISTENING = Color.parseColor("#EF4444")
        private val PROCESSING = Color.parseColor("#3B82F6")
        private val PROCESSING_DIM = Color.parseColor("#1E3A8A")
        private val DONE = Color.parseColor("#22C55E")
        private val ERROR = Color.parseColor("#EF4444")
        private val GLYPH = Color.parseColor("#D1D5DB")
        /** Orange -> bright orange -> red, cycled every 180 ms like the desktop. */
        private val HANDSFREE_PULSE = intArrayOf(
            Color.parseColor("#F97316"), Color.parseColor("#FB923C"), Color.parseColor("#EF4444"),
        )
        private val ACTIVE = setOf(State.LISTENING, State.HANDSFREE, State.PROCESSING)
    }

    var state: State = State.IDLE
        set(value) {
            field = value
            if (value !in ACTIVE) level = 0f
            invalidate()
        }

    /** True while the pill is shown. OverlayService resizes the window to match. */
    var expanded: Boolean = false
        set(value) {
            field = value
            requestLayout()
            invalidate()
        }

    /** Finger is over the [x] during push-to-talk: releasing will discard. */
    var cancelHighlight: Boolean = false
        set(value) {
            if (field != value) {
                field = value
                invalidate()
            }
        }

    private val levels = FloatArray(BARS)
    private var level = 0f
    private val density = resources.displayMetrics.density
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val rect = RectF()

    private fun dp(v: Float) = v * density
    fun compactPx(): Int = dp(COMPACT_DP).roundToInt()
    fun pillPx(): Int = dp(PILL_DP).roundToInt()

    /** Feed one microphone RMS sample (0..1). Called ~20 times a second while recording. */
    fun pushLevel(rms: Float) {
        level = min(1f, rms * LEVEL_GAIN)
        System.arraycopy(levels, 1, levels, 0, BARS - 1)
        levels[BARS - 1] = level
        invalidate()
    }

    fun clearLevels() {
        levels.fill(0f)
        level = 0f
    }

    /** Which control is under an x coordinate (view-local px). Compact mode is all DOT. */
    fun regionAt(x: Float): Region {
        if (!expanded) return Region.DOT
        val cell = dp(COMPACT_DP)
        return when {
            x < cell -> Region.CANCEL
            x > width - cell -> Region.CONFIRM
            else -> Region.DOT
        }
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(if (expanded) pillPx() else compactPx(), compactPx())
    }

    override fun onDraw(canvas: Canvas) {
        val now = SystemClock.uptimeMillis()
        val accent = accentFor(now)
        if (expanded) drawPill(canvas, accent) else drawCompact(canvas, accent, now)
        if (state in ACTIVE) postInvalidateOnAnimation()
    }

    private fun accentFor(now: Long): Int = when (state) {
        State.IDLE -> IDLE
        State.LISTENING -> LISTENING
        State.HANDSFREE -> HANDSFREE_PULSE[((now / 180) % HANDSFREE_PULSE.size).toInt()]
        State.PROCESSING -> PROCESSING
        State.DONE -> DONE
        State.ERROR -> ERROR
    }

    private fun drawCompact(canvas: Canvas, accent: Int, now: Long) {
        val cx = width / 2f
        val cy = height / 2f
        fill.color = BG
        fill.alpha = 245
        canvas.drawCircle(cx, cy, dp(DISC_DP) / 2f, fill)

        if (state == State.ERROR) {
            stroke.color = accent
            stroke.strokeWidth = dp(2f)
            canvas.drawCircle(cx, cy, dp(6f), stroke)
            return
        }

        var r = dp(DOT_DP) / 2f
        var colour = accent
        when (state) {
            State.LISTENING -> r += dp(min(2f, level * 4f))                 // swells with your voice
            State.HANDSFREE -> {
                val phase = ((now / 180) % 2).toInt()
                r += dp(0.5f * phase) + dp(min(1.5f, level * 3f))
            }
            State.PROCESSING -> {                                          // gentle pulse
                val t = (sin(now / 350.0) + 1.0) / 2.0
                colour = blend(PROCESSING_DIM, PROCESSING, t.toFloat())
            }
            else -> {}
        }
        fill.color = colour
        fill.alpha = 255
        canvas.drawCircle(cx, cy, r, fill)
        if (state == State.HANDSFREE) {
            stroke.color = colour
            stroke.strokeWidth = dp(1f)
            canvas.drawCircle(cx, cy, dp(8f), stroke)
        }
    }

    private fun drawPill(canvas: Canvas, accent: Int) {
        val h = height.toFloat()
        val w = width.toFloat()
        val cell = dp(COMPACT_DP)
        val cy = h / 2f

        fill.color = BG
        fill.alpha = 245
        rect.set(0f, 0f, w, h)
        canvas.drawRoundRect(rect, h / 2f, h / 2f, fill)

        // [x] on the left
        val xc = cell / 2f
        val arm = dp(5f)
        stroke.strokeWidth = dp(2f)
        stroke.color = if (cancelHighlight) ERROR else GLYPH
        canvas.drawLine(xc - arm, cy - arm, xc + arm, cy + arm, stroke)
        canvas.drawLine(xc - arm, cy + arm, xc + arm, cy - arm, stroke)

        // [check] on the right
        val kc = w - cell / 2f
        stroke.color = DONE
        canvas.drawLine(kc - dp(6f), cy, kc - dp(1.5f), cy + dp(4.5f), stroke)
        canvas.drawLine(kc - dp(1.5f), cy + dp(4.5f), kc + dp(6f), cy - dp(5f), stroke)

        // waveform bars between them
        val left = cell + dp(4f)
        val right = w - cell - dp(4f)
        val pitch = (right - left) / BARS
        val barW = pitch * 0.55f
        val minH = dp(3f)
        val maxH = dp(22f)
        fill.color = accent
        fill.alpha = 255
        for (i in 0 until BARS) {
            val bh = minH + levels[i] * (maxH - minH)
            val x0 = left + i * pitch + (pitch - barW) / 2f
            rect.set(x0, cy - bh / 2f, x0 + barW, cy + bh / 2f)
            canvas.drawRoundRect(rect, barW / 2f, barW / 2f, fill)
        }
    }

    private fun blend(a: Int, b: Int, t: Float): Int {
        val u = t.coerceIn(0f, 1f)
        fun ch(x: Int, y: Int) = (x + (y - x) * u).roundToInt()
        return Color.rgb(
            ch(Color.red(a), Color.red(b)), ch(Color.green(a), Color.green(b)), ch(Color.blue(a), Color.blue(b)),
        )
    }
}
