package tech.datafying.localflow

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TextSpliceTest {

    @Test
    fun insertsAtCaret() {
        val r = TextSplice.splice("Hello world", 5, 5, ",", separate = false)
        assertEquals("Hello, world", r.text)
        assertEquals(6, r.caret)
    }

    @Test
    fun replacesSelection() {
        val r = TextSplice.splice("Send it to Mark today", 11, 15, "Sarah", separate = false)
        assertEquals("Send it to Sarah today", r.text)
        assertEquals(16, r.caret)
    }

    @Test
    fun reversedSelectionIsNormalised() {
        val r = TextSplice.splice("abc", 3, 1, "X", separate = false)
        assertEquals("aX", r.text)
        assertEquals(2, r.caret)
    }

    @Test
    fun noCaretAppends() {
        val r = TextSplice.splice("abc", -1, -1, "d", separate = false)
        assertEquals("abcd", r.text)
        assertEquals(4, r.caret)
    }

    @Test
    fun nullFieldTreatedAsEmpty() {
        val r = TextSplice.splice(null, 0, 0, "hi", separate = false)
        assertEquals("hi", r.text)
        assertEquals(2, r.caret)
    }

    @Test
    fun pttNeverAddsTrailingWhitespace() {
        val r = TextSplice.splice("", 0, 0, "Hello.", separate = false)
        assertEquals("Hello.", r.text)
        assertEquals("Hello.", r.inserted)
    }

    @Test
    fun handsfreeSeparatorAddedWhenPreviousTextEndsWithoutWhitespace() {
        val r = TextSplice.splice("First sentence.", 15, 15, "Second one.", separate = true)
        assertEquals("First sentence. Second one.", r.text)
        assertEquals(" Second one.", r.inserted)
        assertEquals(27, r.caret)
    }

    @Test
    fun handsfreeSeparatorSkippedAfterSpaceOrNewline() {
        assertEquals("First. Second", TextSplice.splice("First. ", 7, 7, "Second", separate = true).text)
        assertEquals("First.\nSecond", TextSplice.splice("First.\n", 7, 7, "Second", separate = true).text)
    }

    @Test
    fun handsfreeSeparatorSkippedInEmptyField() {
        val r = TextSplice.splice("", 0, 0, "Second", separate = true)
        assertEquals("Second", r.text)
    }

    @Test
    fun separatorLooksOnlyAtTextBeforeCaret() {
        // Caret in the middle of a word: the text before it ends without whitespace.
        val r = TextSplice.splice("ab cd", 2, 2, "X", separate = true)
        assertEquals("ab X cd", r.text)
    }

    @Test
    fun caretOutOfRangeIsClamped() {
        val r = TextSplice.splice("abc", 10, 10, "d", separate = false)
        assertEquals("abcd", r.text)
    }

    @Test
    fun setupLineParses() {
        val p = Settings.parseSetupLine("http://localflow-pc.tail1234.ts.net|abc123")
        assertEquals("http://localflow-pc.tail1234.ts.net", p?.first)
        assertEquals("abc123", p?.second)
        assertEquals("http://pc.ts.net", Settings.parseSetupLine(" pc.ts.net/ | tok \n")?.first)
        assertNull(Settings.parseSetupLine("just some text"))
        assertNull(Settings.parseSetupLine("http://x.ts.net|"))
        assertNull(Settings.parseSetupLine(null))
    }
}
