package tech.datafying.localflow

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class WavTest {

    private fun le(buf: ByteArray) = ByteBuffer.wrap(buf).order(ByteOrder.LITTLE_ENDIAN)

    @Test
    fun headerIsCanonical16kMonoPcm() {
        val pcm = ByteArray(3200) { (it % 251).toByte() }   // 0.1 s
        val wav = Wav.build(pcm)
        assertEquals(44 + pcm.size, wav.size)

        assertEquals("RIFF", String(wav, 0, 4, Charsets.US_ASCII))
        assertEquals(36 + pcm.size, le(wav).getInt(4))
        assertEquals("WAVE", String(wav, 8, 4, Charsets.US_ASCII))
        assertEquals("fmt ", String(wav, 12, 4, Charsets.US_ASCII))
        assertEquals(16, le(wav).getInt(16))
        assertEquals(1.toShort(), le(wav).getShort(20))        // PCM
        assertEquals(1.toShort(), le(wav).getShort(22))        // mono
        assertEquals(16_000, le(wav).getInt(24))               // sample rate
        assertEquals(32_000, le(wav).getInt(28))               // byte rate
        assertEquals(2.toShort(), le(wav).getShort(32))        // block align
        assertEquals(16.toShort(), le(wav).getShort(34))       // bits
        assertEquals("data", String(wav, 36, 4, Charsets.US_ASCII))
        assertEquals(pcm.size, le(wav).getInt(40))
        assertArrayEquals(pcm, wav.copyOfRange(44, wav.size))
    }

    @Test
    fun emptyPayloadStillHasValidHeader() {
        val wav = Wav.build(ByteArray(0))
        assertEquals(44, wav.size)
        assertEquals(36, le(wav).getInt(4))
        assertEquals(0, le(wav).getInt(40))
    }

    @Test
    fun durationMatchesSampleMath() {
        assertEquals(1f, Wav.durationSeconds(32_000), 1e-6f)
        assertEquals(0.25f, Wav.durationSeconds(8_000), 1e-6f)
    }
}
