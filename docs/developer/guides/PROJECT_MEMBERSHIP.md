# Project membership provisioning

Project sharing requires both project membership and the artifact's group access
rules. Selecting **Available to FB** does not grant project membership.

Operators may set `DEFAULT_PROJECT_ID` to an existing active project UUID when
all newly authenticated users are authorized to join that project. New accounts
and their member-level memberships are committed together. Empty configuration
(the default) leaves enrollment under operator control; an invalid UUID or an
inactive/missing project rejects new-account provisioning without saving a partial
account. The setting never selects an arbitrary project or creates one.

Existing accounts retain their memberships and roles unchanged. Login does not
backfill missing memberships, including intentionally revoked memberships. Repair
existing accounts through a separately reviewed, explicit data change: inventory
identities and existing access, approve the exact users/project, rehearse on an
isolated copy, and apply within the deployment's backup/maintenance procedure.

Membership allows access to project-shared content, subject to its group rules.
It does not share private agents or flows, change their instructions, or remove
FB/group restrictions. After a membership repair, the owner can reopen the agent,
choose **Shared with project**, choose **Available to FB**, and save. Verify the
intended colleagues have membership and the appropriate authenticated group.
