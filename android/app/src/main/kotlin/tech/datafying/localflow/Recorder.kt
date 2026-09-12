package tech.datafying.localflow

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

/**
 * Captures 16 kHz mono 16-bit PCM into memory and reports the RMS level of every 50 ms chunk
 * (so ~20 updates a second for the waveform). Callbacks arrive on the capture thread.
 */
class Recorder(
    private val maxMillis: Long,
    private val onLevel: (Float) -> Unit,
    private val onLimit: () -> Unit,
) {
    companion object {
        const val SAMPLE_RATE = 16_000
        private const val TAG = "LocalFlow.Recorder"
        private const val CHUNK_SAMPLES = SAMPLE_RATE / 20   // 50 ms
    }

    private var record: AudioRecord? = null
    private var thread: Thread? = null
    @Volatile private var running = false
    private val out = ByteArrayOutputStream()

    val isRecording: Boolean get() = running

    /** Milliseconds captured so far. */
    val elapsedMillis: Long get() = synchronized(out) { out.size() } * 1000L / (SAMPLE_RATE * 2)

    /** Requires RECORD_AUDIO to have been granted; the caller checks. Returns false if the mic is unavailable. */
    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (running) return true
        val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minBuf <= 0) return false
        val bufSize = maxOf(minBuf, SAMPLE_RATE)   // >= 0.5 s of audio
        val rec = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufSize,
            )
        } catch (e: Exception) {
            Log.w(TAG, "AudioRecord init failed", e)
            return false
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            return false
        }
        synchronized(out) { out.reset() }
        record = rec
        running = true
        try {
            rec.startRecording()
        } catch (e: IllegalStateException) {
            running = false
            rec.release()
            record = null
            return false
        }
        thread = Thread({ loop(rec) }, "localflow-rec").also { it.start() }
        return true
    }

    private fun loop(rec: AudioRecord) {
        val chunk = ShortArray(CHUNK_SAMPLES)
        val bytes = ByteArray(CHUNK_SAMPLES * 2)
        val maxBytes = maxMillis * SAMPLE_RATE * 2 / 1000
        while (running) {
            val n = rec.read(chunk, 0, chunk.size)
            if (n < 0) {
                Log.w(TAG, "read error $n")
                break
            }
            if (n == 0) continue
            var sum = 0.0
            for (i in 0 until n) {
                val s = chunk[i].toDouble()
                sum += s * s
            }
            val rms = (sqrt(sum / n) / 32768.0).toFloat()
            ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().put(chunk, 0, n)
            val total = synchronized(out) {
                out.write(bytes, 0, n * 2)
                out.size()
            }
            onLevel(rms)
            if (total >= maxBytes) {
                onLimit()
                break
            }
        }
    }

    /** Stops capture and returns everything recorded as raw PCM. Safe to call twice. */
    fun stop(): ByteArray {
        running = false
        thread?.let { t ->
            try { t.join(700) } catch (ignored: InterruptedException) {}
        }
        thread = null
        record?.let { r ->
            try { r.stop() } catch (ignored: IllegalStateException) {}
            r.release()
        }
        record = null
        return synchronized(out) { out.toByteArray() }
    }

    fun discard() {
        stop()
        synchronized(out) { out.reset() }
    }
}
