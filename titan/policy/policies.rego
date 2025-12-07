# Path: FLOW/titan/policy/policies.rego

package titan.policy

# Default deny — only explicit allow conditions let operations pass.
default allow = false

# Trust levels:
#   0 = untrusted
#   1 = basic
#   2 = elevated
#   3 = system/internal
#
# SessionManager + Identity assign trust levels.

# ─────────────────────────────────────────────────────────────
# BASIC ALLOW RULES
# ─────────────────────────────────────────────────────────────

# Allow sandbox execution only for trust_level >= 1
allow {
    input.action_type == "sandbox"
    input.trust_level >= 1
}

# Allow HostBridge only for trust_level >= 2
allow {
    input.action_type == "hostbridge"
    input.trust_level >= 2
}

# Plugins: safe subset only
allow {
    input.action_type == "plugin"
    input.module == "safe"           # Only approved plugins
    input.trust_level >= 1
}

# ─────────────────────────────────────────────────────────────
# ARGUMENT VALIDATION RULES
# ─────────────────────────────────────────────────────────────

# Enforce max command length
deny[msg] {
    input.action_type == "sandbox"
    count(input.command) > 2000
    msg := "Command too long"
}

# For HostBridge, all paths must be validated by engine before reaching OPA.
# Rego can enforce structural constraints if needed.
deny[msg] {
    input.action_type == "hostbridge"
    input.args[k] == "/"
    msg := sprintf("Invalid argument value for key %v", [k])
}

# ─────────────────────────────────────────────────────────────
# IDENTITY-BASED RULES
# ─────────────────────────────────────────────────────────────

deny[msg] {
    input.trust_level == 0
    input.action_type == "hostbridge"
    msg := "Untrusted users cannot use HostBridge"
}

# ─────────────────────────────────────────────────────────────
# FINAL ALLOW RULE
# If no deny matched AND an allow rule matched → allow.
# Otherwise default deny.
# ─────────────────────────────────────────────────────────────
