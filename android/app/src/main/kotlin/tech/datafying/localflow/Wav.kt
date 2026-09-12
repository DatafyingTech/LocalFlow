package tech.datafying.localflow

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Wraps raw little-endian PCM in a canonical 44-byte RIFF/WAVE header. Pure Kotlin, unit-tested. */
object Wav {
    const val HEADER_SIZE = 44

    fun build(pcm: ByteArray, sampleRate: Int = 16_000, channels: Int = 1, bitsPerSample: Int = 16): ByteArray {
        require(channels > 0 && bitsPerSample % 8 == 0 && sampleRate > 0) { "bad WAV format" }
        val blockAlign = channels * bitsPerSample / 8
        val byteRate = sampleRate * blockAlign
        val buf = ByteBuffer.allocate(HEADER_SIZE + pcm.size).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(ascii("RIFF"))
        buf.putInt(36 + pcm.size)
        buf.put(ascii("WAVE"))
        buf.put(ascii("fmt "))
        buf.putInt(16)                       // PCM fmt chunk size
        buf.putShort(1)                      // audio format 1 = PCM
        buf.putShort(channels.toShort())
        buf.putInt(sampleRate)
        buf.putInt(byteRate)
        buf.putShort(blockAlign.toShort())
        buf.putShort(bitsPerSample.toShort())
        buf.put(ascii("data"))
        buf.putInt(pcm.size)
        buf.put(pcm)
        return buf.array()
    }

    /** Seconds of audio in a raw 16-bit mono PCM buffer. */
    fun durationSeconds(pcmBytes: Int, sampleRate: Int = 16_000): Float =
        pcmBytes / 2f / sampleRate

    private fun ascii(s: String) = s.toByteArray(Charsets.US_ASCII)
}
