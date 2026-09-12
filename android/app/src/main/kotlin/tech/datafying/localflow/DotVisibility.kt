package tech.datafying.localflow

/**
 * Decides whether the floating dot should be on screen. Pure Kotlin so it can be unit-tested;
 * OverlayService owns the debounce and the actual view toggling.
 *
 * Like Wispr Flow, the dot is only shown when the user can type: an editable field has focus or
 * the keyboard is up. That knowledge comes from the accessibility service (InsertionService),
 * so without it the dot has to stay visible everywhere.
 */
object DotVisibility {

    /** States during which the dot must never disappear from under the user's finger or eyes. */
    private val PINNED = setOf(
        DotView.State.LISTENING, DotView.State.HANDSFREE, DotView.State.PROCESSING, DotView.State.DONE,
    )

    /**
     * @param canType an editable node has input focus, or an input-method window is showing.
     * @param state the dot's current state.
     * @param showOnlyWhenTyping the user's "Show the dot only when a text field is active" setting.
     * @param serviceEnabled the accessibility service is connected, so [canType] is meaningful.
     * @return true = the dot should be visible.
     */
    fun decide(canType: Boolean, state: DotView.State, showOnlyWhenTyping: Boolean, serviceEnabled: Boolean): Boolean {
        if (!showOnlyWhenTyping) return true
        if (!serviceEnabled) return true
        if (state in PINNED) return true
        return canType
    }
}
