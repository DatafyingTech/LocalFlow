package tech.datafying.localflow

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * "PC not reachable, is Tailscale on?" was the answer to three unrelated problems, and it was
 * wrong for two of them: after a Windows sign-out the PC was on the tailnet and perfectly
 * reachable, it just had no LocalFlow running on it. These pin the three verdicts.
 */
class NetworkErrorTest {

    @Test
    fun `name that does not resolve blames Tailscale on the phone`() {
        val e = UnknownHostException("Unable to resolve host \"pc.tailnet.ts.net\": No address associated with hostname")
        assertEquals(ApiClient.Kind.NO_DNS, ApiClient.kindForIo(e))
        assertEquals(ApiClient.MSG_NO_DNS, ApiClient.messageFor(ApiClient.kindForIo(e)))
    }

    @Test
    fun `connection refused means the PC is up but LocalFlow is not`() {
        val e = ConnectException("Failed to connect to /100.64.0.2:8770: connect failed: ECONNREFUSED (Connection refused)")
        assertEquals(ApiClient.Kind.REFUSED, ApiClient.kindForIo(e))
        assertEquals(ApiClient.MSG_REFUSED, ApiClient.messageFor(ApiClient.kindForIo(e)))
    }

    @Test
    fun `econnrefused without the word refused still counts`() {
        assertEquals(ApiClient.Kind.REFUSED, ApiClient.kindForIo(ConnectException("connect failed: ECONNREFUSED (22)")))
    }

    @Test
    fun `connect timeout means the PC is off or asleep`() {
        val e = SocketTimeoutException("failed to connect to pc.tailnet.ts.net after 5000ms")
        assertEquals(ApiClient.Kind.UNREACHABLE, ApiClient.kindForIo(e))
        assertEquals(ApiClient.MSG_UNREACHABLE, ApiClient.messageFor(ApiClient.kindForIo(e)))
    }

    @Test
    fun `no route means the PC is off or asleep`() {
        assertEquals(ApiClient.Kind.UNREACHABLE, ApiClient.kindForIo(NoRouteToHostException("No route to host")))
        assertEquals(ApiClient.Kind.UNREACHABLE, ApiClient.kindForIo(ConnectException("connect failed: ETIMEDOUT")))
    }

    @Test
    fun `an unexplained io error falls back to the generic message`() {
        assertEquals(ApiClient.Kind.NETWORK, ApiClient.kindForIo(IOException("unexpected end of stream")))
        assertEquals(ApiClient.MSG_NETWORK, ApiClient.messageFor(ApiClient.Kind.NETWORK))
    }

    @Test
    fun `every kind has its own sentence`() {
        val msgs = ApiClient.Kind.entries.map { ApiClient.messageFor(it) }
        assertEquals("no two kinds share a message", msgs.size, msgs.toSet().size)
        assertEquals("no blank messages", 0, msgs.count { it.isBlank() })
    }

    @Test
    fun `the three network kinds say something different from each other`() {
        val three = listOf(ApiClient.Kind.NO_DNS, ApiClient.Kind.REFUSED, ApiClient.Kind.UNREACHABLE)
            .map { ApiClient.messageFor(it) }
        assertEquals(3, three.toSet().size)
        assertEquals(listOf(ApiClient.MSG_NO_DNS, ApiClient.MSG_REFUSED, ApiClient.MSG_UNREACHABLE), three)
    }

    @Test
    fun `only timeouts keep the audio for a retry`() {
        val timeout = ApiClient.ApiException(ApiClient.Kind.TIMEOUT, "busy")
        val refused = ApiClient.ApiException(ApiClient.Kind.REFUSED, ApiClient.MSG_REFUSED)
        assertEquals(true, timeout.retryable)
        assertEquals(false, refused.retryable)
    }
}
