package tech.datafying.localflow

/**
 * Pure text-splicing rules from docs/API.md, kept free of Android types so they can be unit-tested.
 *
 * - Insert at the caret; replace the selection if there is one.
 * - `separate` = this is a hands-free result following another hands-free result: put one space
 *   before it unless the text before the caret is empty or already ends in whitespace/newline.
 * - Never append a trailing space or newline.
 */
object TextSplice {
    data class Result(
        /** Full new field contents. */
        val text: String,
        /** Where the caret should go afterwards (just after the inserted piece). */
        val caret: Int,
        /** The exact characters that were added (separator included). */
        val inserted: String,
    )

    fun splice(existing: CharSequence?, selStart: Int, selEnd: Int, insert: String, separate: Boolean): Result {
        val base = existing?.toString() ?: ""
        var start = selStart
        var end = selEnd
        if (start < 0 || end < 0) {            // no caret reported: append
            start = base.length
            end = base.length
        }
        if (start > end) start = end.also { end = start }
        start = start.coerceIn(0, base.length)
        end = end.coerceIn(0, base.length)

        val before = base.substring(0, start)
        val after = base.substring(end)
        val piece = if (needsSeparator(before, separate)) " $insert" else insert
        return Result(before + piece + after, before.length + piece.length, piece)
    }

    fun needsSeparator(before: String, separate: Boolean): Boolean =
        separate && before.isNotEmpty() && !before.last().isWhitespace()
}
