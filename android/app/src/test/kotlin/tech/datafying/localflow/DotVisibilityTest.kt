package tech.datafying.localflow

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import tech.datafying.localflow.DotView.State

class DotVisibilityTest {

    @Test
    fun settingOffAlwaysShows() {
        for (s in State.values()) {
            assertTrue(DotVisibility.decide(canType = false, state = s, showOnlyWhenTyping = false, serviceEnabled = true))
            assertTrue(DotVisibility.decide(canType = false, state = s, showOnlyWhenTyping = false, serviceEnabled = false))
        }
    }

    @Test
    fun noAccessibilityServiceAlwaysShows() {
        for (s in State.values()) {
            assertTrue(DotVisibility.decide(canType = false, state = s, showOnlyWhenTyping = true, serviceEnabled = false))
        }
    }

    @Test
    fun idleFollowsCanType() {
        assertTrue(DotVisibility.decide(canType = true, state = State.IDLE, showOnlyWhenTyping = true, serviceEnabled = true))
        assertFalse(DotVisibility.decide(canType = false, state = State.IDLE, showOnlyWhenTyping = true, serviceEnabled = true))
    }

    @Test
    fun errorFollowsCanType() {
        // A red ring that is waiting for a retry tap comes back as soon as a field is focused again.
        assertTrue(DotVisibility.decide(canType = true, state = State.ERROR, showOnlyWhenTyping = true, serviceEnabled = true))
        assertFalse(DotVisibility.decide(canType = false, state = State.ERROR, showOnlyWhenTyping = true, serviceEnabled = true))
    }

    @Test
    fun activeStatesNeverHide() {
        for (s in listOf(State.LISTENING, State.HANDSFREE, State.PROCESSING, State.DONE)) {
            assertTrue("$s", DotVisibility.decide(canType = false, state = s, showOnlyWhenTyping = true, serviceEnabled = true))
        }
    }
}
