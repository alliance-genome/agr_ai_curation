# Curator AI Chat capability audit

Tracking: ALL-1057 / KANBAN-1644. This records implemented capabilities and their verification boundaries. ALL-1056 separately owns guided flow creation, reliable Apply and readable flow proposals. Both will deploy together to dev.

## Evidence standard

A capability needs a provider-visible tool, working dispatch, current authenticated context, meaningful results, and an editor or persistence path where applicable. Schema enums and prompt promises alone are insufficient. Test proposed changes against actual resulting fields and immutable revision pins. Explain saved configuration separately from unsaved drafts and observed execution results.

## Workshop action coverage

| Curator request or control | Existing runtime path | Audit status / remaining work |
| --- | --- | --- |
| Explain the agent's purpose and prompt | `refresh_workshop_prompt` main/group/metadata; `get_prompt` for templates | Existing bounded exact reads; test explanation uses current text |
| Rename, describe or change icon | `propose_workshop_draft_update`: set_name, set_description, set_icon | Existing typed edits, shared Save validation |
| Change the main instructions | set_instructions | Existing; preserve custom output and unrelated prompts |
| Change group instructions or restore inherited rules | set_group_instructions, reset_group_instructions, set_include_group_rules | Existing; check discovery and inherited access boundaries |
| Choose a model and reasoning level | search/detail catalog; select_model with reasoning | Existing; model options are catalog-backed, not hardcoded |
| Explain, add or remove a runtime tool | get_tool_inventory/get_tool_details; add_tool/remove_tool | Existing; template-owned mandatory tools remain protected |
| Request a tool the platform lacks | Workshop Tool Request dialog | Chat action opens the existing Tool Request dialog; requesting a tool does not install one |
| Choose/clear structured output | select_output/clear_output; exact authorized output catalog | Existing; canonical validation blocks incompatible tools/output |
| Name the item type and add extraction guidance | edit_profile set_basics/update_basics | Existing; profile description supplements extraction prompt |
| Add, rename, reorder or remove details | add/update/replace/remove/reorder field | Existing; resolve display names within their parent to stable keys |
| Change answer format, choices or instructions | update_field with value_schema/description | Existing; conversion must be explicit when it discards content |
| Always include a detail; allow an empty answer | update_field required/nullable | Existing separate flags; explain absence versus an empty value with concrete examples |
| Add or edit parts of one answer | field_path + add/update/reorder/remove field | Existing one-level object parts; no repeated answers or parts inside parts |
| See example data | inspect_workshop_profile preview | Existing; placeholders are not paper evidence |
| Discover a built-in or custom validator | inspect_workshop_profile validator_options; capability catalog | Existing authorized compatibility results and immutable custom pins |
| Attach/edit/remove validator on a particular part | set_mapping/remove_mapping | Existing canonical mapping path; targeted nested custom-validator tests exist; exact originating-step revision return implemented; deployed journey verification remains required |
| Explain a validator's purpose, inputs and limitations | capability detail; get_domain_pack_validation_plan; saved revision read | Distinguish structural conformance, semantic findings and submission readiness |
| Explain an actual validation failure | trace/evidence/domain review tools | Audit exact run access and scope; configuration alone cannot prove why a run failed |
| Reuse a saved structure | list_saved/saved_revision then select_output | Existing reauthorization of exact profile revision |
| Inspect saved agent versions and compare settings | new inspect_saved_studio_resource agent_revisions/agent_revision | Implemented typed service-backed read; includes the exact pinned custom structure and preserves revision/access arguments |
| List or inspect saved flows | new inspect_saved_studio_resource list_flows/flow | Implemented owner/active filters and bounded continuation; never loads editor |
| Inspect platform code while editing | search_codebase/read_source_file | Enabled across authoring tabs; source-only path policy excludes deployment/private files |
| Create from scratch or a template; clone an agent | Workshop start screen and draft.startDraft | request_workshop_action opens scratch/template/authorized clone drafts; no persistence until explicit Save |
| Open an existing custom agent from a flow | Page currently only exposes Agent browser from node panel | Chat action opens the owned custom agent with exact node/agent/revision origin; ambiguous repeated uses require a selected step |
| Save / Save As | Explicit existing dialogs and shared Save APIs | Chat action opens the existing Save / Save As dialog; explicit curator confirmation remains required |
| Inspect/restore historical version | Versions section and explicit restore dialog | Exact revision reads plus Chat navigation to Versions; restore remains an explicit UI action |
| Sharing and group restrictions | set_visibility/set_allowed_groups | Existing typed edits, inherited access floor and Save authorization |
| Manage/archive an agent | Explicit Manage/Delete controls | Chat opens Manage controls; archive/delete still requires the existing explicit UI confirmation |
| Return the saved agent to the same flow step | existing retarget_agent_revision backend operation | Review in Flow proposes retargeting only the originating node to the exact just-saved revision; later head changes fail closed; new/clone drafts propose adding a step |

## Curator question scenarios

| Example | Grounding and expected behavior |
| --- | --- |
| “What happens after the initial instructions?” | Read current topology and named nodes; explain connections and output branches without inventing steps |
| “Does this description change extraction?” | Read current draft and source/contract when needed; explain supplemental guidance in addition to agent and detail prompts |
| “Only change the supplier number instructions.” | Resolve the part within its parent; update description only; preserve keys, flags, sibling parts and validators |
| “Why is this required?” | Explain current required flag and prompt independently; show a concrete missing-value example; offer the requested edit |
| “Which validator should I use here?” | Discover compatible inputs and authorized built-in/custom options; explain limitations; no fabricated validation for an unsupported type |
| “Use my validator on catalog number, not supplier name.” | Map the exact part and custom revision; preview effects and preserve other mappings |
| “Why did this validator reject the value?” | Locate the authorized run and its actual findings/evidence; distinguish no result, skipped execution, structural errors and semantic findings |
| “Which agent version will this flow run?” | Read the flow's exact pin, then the authorized saved snapshot; do not substitute catalog head |
| “What changed since the version in this flow?” | Compare two exact authorized snapshots and explain prompt/tools/output/validator differences |
| “Make a copy so I can experiment.” | Open a clone draft, preserve provenance/access floor, and require explicit Save for the new agent |
| “Update this agent but leave the other flow alone.” | Save a new immutable revision and propose retargeting only the originating node; other pins stay intact |
| “I canceled—did anything save?” | Report actual Apply/Save state; never infer persistence from successful proposal generation |
| “How does the program decide this?” | Inspect deployed source when needed; describe behavior plainly and identify inference or unavailable evidence |

## Current verification

- 297 focused backend tests pass across tools, authenticated dispatch, source reads, saved resources, action reauthorization, nested field mappings, proposal validation and exact revision retargeting.
- Database inspection is typed, rejects arbitrary SQL/write actions, scopes saved flows to the authenticated owner, uses existing revision authorization, and runs inside a PostgreSQL read-only transaction.
- Source reads and search share a path boundary; regressions cover deployment files, private paths, symlinks and explicit search globs. Live testing exposed incorrect glob anchoring outside the repository cwd; both search modes now run from the source root, with four real-rg regressions. Saved revision inspection includes its authorized pinned profile so field questions do not require editor navigation.
- Chat actions are prepared without side effects, reauthorized on click and rejected when the draft, tab, source or saved revision changes. Lifecycle actions wait for Save/authoring/hydration and explicitly reset discarded drafts even for the same source.
- Focused frontend regressions cover readable proposals, Apply conflicts, action buttons, same-source resets, pending Save, tab changes during validation and exact-step return with repeated uses of the same agent.
- Live dev discovery: internal attachment export policy was removed by the browser but included in proposal fingerprints. The compiler now emits the same persistence form; both flag values fail the regression before the fix and pass afterward. Focused corrective backend gate: 133 passed.
- Live Workshop Save after a part-validator change exposed stale attachment identities on revision retarget. Explicit retargets now reconcile selections against the authorized new revision, preserve matching opt-outs, and hydrate new defaults. Other uses and ordinary validation remain strict; 135 focused backend tests passed, including profile changes and removal of all attachments.
- GPT-6 Astra medium pre-deploy review: ACCEPT after lifecycle fixes. Combined frontend/build/type results and deployed conversational evidence are recorded in the linked ticket/workpad; code review alone does not establish live acceptance.

## Deliberate UI boundaries

Chat can prepare edits, open existing controls and explain their effect. Applying a proposal changes the draft; Save persists it. Historical restore, destructive management and Save confirmation use their existing explicit UI controls. Inspection never silently loads or changes an editor. Runtime findings require accessible run evidence; saved configuration and source code alone cannot establish whether a validator ran or why it rejected a value.

## Dev usability refinements

Successful Flow and Workshop proposal Apply schedules one Chat follow-up after the updated draft renders. The follow-up captures current editor context and asks the agent to continue the discussed next step, or briefly confirm completion. Cancellation, validation failure, and failed Apply do not continue. A changed tab/session or another active send supersedes the pending follow-up. Apply still changes only the draft; Save remains explicit. The review button displays Preparing while the proposal response streams and Applying during the editor operation.

Workshop Start now promotes Custom data extraction. It selects an available template declaring unprofiled generic output, retains its tools/model settings, initializes a fresh profile-bound structure, and opens the item-type wizard. Help distinguishes custom records from standard Alliance structures. Support labels use installed extraction metadata and model/object/schema definition states; an active domain pack alone does not establish readiness.

AI Chat uses its own registered model selection: `AGENT_STUDIO_OPENAI_MODEL`
(default `gpt-6-astra`) and `AGENT_STUDIO_REASONING_EFFORT` (default `medium`).
Backend restart applies changes. The shared extraction/routing default stays
independent. Astra uses Responses tool calling and catalog capability metadata
that disables temperature; unsupported reasoning levels fail configuration.
Prompt guidance follows OpenAI's [Astra migration and prompting guide](https://developers.openai.com/api/docs/guides/latest-model),
reviewed September 6, 2026, while retaining curator-controlled Apply/Save and
one-decision-at-a-time authoring.
