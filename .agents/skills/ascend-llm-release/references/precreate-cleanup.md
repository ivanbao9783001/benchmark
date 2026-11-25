# Pre-create failure cleanup

Local and SSH node cleanup no longer release claims based only on an empty
coordinator container map. HostGuard records effect_kind with durable command
acceptance. release_unstarted checks the owner's host-wide command history while
holding the resource lock. It releases only matching claims and only when the
history proves that no side effect started.

Acceptance of a create command is not proof of an effect: the node runs
read-only preflight (occupancy probes, device-map checks, identity and port
validation) after acceptance. New records carry `effect_started=false`, and the
node durably writes `effects/<digest([owner,command_id])>.json` immediately
before its first mutating call. A create record without that marker means the
preflight rejected the request and nothing was created, so claims are released
and cleanup_status is VERIFIED. A create record with the marker, an unknown
effect, or a legacy record lacking the field keeps claims locked and requires
resource inventory verification. Successful pre-create cleanup records its basis
as no durable side effect.

The remote worker derives resource identities from frozen groups and uses the
same host guard; the control side cannot supply an arbitrary set to unlock.
Tests exercise persistent claims, unknown create outcomes and conflicting tasks
locally. This does not establish actual Docker/SSH crash recovery on a server.
