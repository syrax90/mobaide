package com.mobaide.app.ui.util

/**
 * Mask an email address for display: keep the first character of the local
 * part and the full domain, replace the middle of the local part with
 * bullets. Safe for empty input and short local parts.
 *
 * Examples:
 *   "alice@example.com"  → "a••••e@example.com"
 *   "a@example.com"      → "a•••@example.com"
 *   "ab@example.com"     → "a•@example.com"
 *   "abc@example.com"    → "a••@example.com"
 *   ""                   → ""
 */
internal fun maskEmail(email: String): String {
    val at = email.indexOf('@')
    if (at <= 0) return email
    val local = email.substring(0, at)
    val domain = email.substring(at)
    val masked = when {
        local.length <= 1 -> local + "•".repeat(3)
        local.length <= 3 -> local.first() + "•".repeat(local.length - 1)
        else -> local.first() + "•".repeat(local.length - 2) + local.last()
    }
    return masked + domain
}
