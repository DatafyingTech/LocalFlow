package tech.datafying.localflow

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerUrlTest {

    @Test
    fun blankIsReportedAsNotSetUp() {
        assertEquals(ApiClient.UrlProblem.BLANK, ApiClient.classifyUrl(""))
        assertEquals(ApiClient.UrlProblem.BLANK, ApiClient.classifyUrl("   \n"))
        assertEquals(ApiClient.MSG_URL_BLANK, ApiClient.urlProblemMessage(""))
    }

    @Test
    fun malformedIsReportedAsWrongShape() {
        for (bad in listOf("http://", "not a url", "ftp://pc.ts.net", "http://pc name.ts.net")) {
            assertEquals(bad, ApiClient.UrlProblem.MALFORMED, ApiClient.classifyUrl(bad))
            assertEquals(bad, ApiClient.MSG_URL_MALFORMED, ApiClient.urlProblemMessage(bad))
        }
    }

    @Test
    fun goodUrlsHaveNoProblem() {
        for (ok in listOf("http://pc-name.tailnet.ts.net", "https://pc.ts.net", "http://100.64.0.1", "http://pc.ts.net:8770")) {
            assertEquals(ok, ApiClient.UrlProblem.NONE, ApiClient.classifyUrl(ok))
            assertNull(ok, ApiClient.urlProblemMessage(ok))
        }
    }

    @Test
    fun messagesNeverMentionAToken() {
        assertEquals(false, ApiClient.MSG_URL_BLANK.contains("token", ignoreCase = true))
        assertEquals(false, ApiClient.MSG_URL_MALFORMED.contains("token", ignoreCase = true))
    }
}
