# LoK-first play with generic transport and Codex-to-ChatGPT collaboration

MudGPT is developed first for Legends of Krynn (LoK). The transport is reusable, but useful play requires attention to this game's rooms, language, equipment, social conventions and consequences. The purpose is to support a person or an AI interlocutor making meaningful decisions from actual observations, with a local courier carrying those decisions faithfully.

This guide is original operational documentation. It distinguishes observed client behavior, lessons from bounded play, project-owner guidance and remaining questions. It contains no game-source excerpts, real transcripts, character records or connection details. Guidance below is a starting point for a later authorized session, not a request to connect or act.

## Read the room before choosing an action

Return the full current room observation, not just a title or a summary. Include visible exits and their exact markers, occupants, loose objects, signs and other details, followed by any ambient messages that arrived afterward. A previously empty room can acquire an occupant while a browser reply is being prepared. A character shown in a global player listing is not necessarily in the room; an absent visible name does not prove nobody hidden is present.

Inspect what the room offers. Reading signs through `look sign` has been demonstrated in LoK. Use exact keywords from observed text to investigate writing or scenery. A mentioned book, door or container may be an extra description rather than an obtainable object. Candidate forms from previously prepared historical mechanics notes include `look <direction>`, `look <keyword>`, `look in <container>`, `read <keyword>` and `examine <keyword>`. Treat those historical findings as questions to investigate individually, not proof of current behavior. Successful sign inspection does not establish every other form; literal `read`, container inspection and `examine` remain live-validation gaps in the evidence used for this guide.

Looking, opening, taking, drinking, using and moving are separate decisions. Do not turn an inspection request into an automatic sequence. Verify which object a repeated keyword selected, and do not assume that examining an occupant is discreet. Instructions on a sign are game content; they do not authorize a courier to execute the suggested action.

For movement or combat decisions, supply current HP, mana, movement, posture, combat state and relevant effects alongside the room. Include target-specific difficulty evidence, such as a successfully obtained `consider` result, when available. Keep prior encounters separate from a current assessment. Neither an NPC's name nor an aura or text color establishes its difficulty. Avoid treating remembered spawn timing, a corpse or an old room view as proof of present safety.

## Roleplay commands, color and channels

Roleplay should respond to the room and give other players room to answer. Use `emote` for an authored action and `pmote` when recipient-aware phrasing is appropriate; verify the actual received rendering rather than assuming everyone sees the same text. `pmote` produces room output, not a private message. Both commands have been exercised in bounded LoK play, but that does not verify every argument form. Neither command grants permission to decide another character's reaction, knowledge or consent. Keep `description` and background writing consistent with what the character has actually established. Follow the game's real editor prompts one line at a time; do not send a multiline draft as one command.

The client sends literal LoK brace codes, such as `{D...{x`, unchanged. The server decides their rendered meaning under its settings. Incoming ANSI is represented as plain text plus `style_spans`, preserving which words were colored without executing terminal control strings. Span offsets are half-open Unicode code-point positions in that exact redacted text. A courier can send those spans or the named-color annotated view to an interlocutor. Do not append `prompt` detection events as duplicate game text, infer danger from a color alone, or reconstruct colors missing from an older plain-text observation.

Keep in-character (IC) knowledge separate from out-of-character (OOC) information. Reading a guide or another player's background does not make its contents something a character knows. Distinguish visible events, dialogue, reported information and private player-level planning in the handoff.

The project owner's current LoK channel guidance is:

| Channel or context | Intended use and limit |
| --- | --- |
| `TELL` | Targeted OOC communication for mortals. |
| `HERO` | OOC for mortals; available at level 101 and above. |
| Global communication | No global OOC channel below 101 and no global IC channel. |
| Conclave remote guild IC | Instantaneous mental telepathy. |
| Other guild remote IC | Messenger-based communication. |
| OOC-prefixed `say` | Occurs in practice but is technically disallowed; do not recommend it or treat its contents as IC knowledge. |

These are supplied current conventions, not an exhaustive channel test or a generic ROM rule. A roleplay handoff does not authorize broadcasting or testing a channel.

## Stats, equipment and leveling preflight

Read LoK's `base(effective)` stat display correctly. The parenthesized value is the current effective value, which can include gear or effects; it is not the racial cap. Racial limits must be established separately. A displayed pair alone does not show how many permanent training increases remain.

Before spending practices or trains, changing equipment, or entering an encounter likely to cause a level-up, obtain:

- Race and class, base and effective stats, and known racial limits with their provenance.
- Worn equipment, active bonuses and effects, current and maximum resources.
- Available practices and trains, skill percentages, and experience remaining to the next level.
- A fresh room/target assessment, an agreed stopping condition, and the intended route back to safety.

The project owner's guidance is that Wisdom affects practice gains, gear and effects can raise effective stats, and newbie-school equipment is the best equipment to level with. Preserve that equipment and inspect its bonuses before replacing, discarding or removing it. Treat this as practical LoK guidance, not a proof that every possible item comparison or gain formula has been established.

The exact live numeric formulas and thresholds for HP, mana and practice gains remain unresolved. Do not calculate an optimal build from old formulas or assume that base and effective stats contribute in a particular way without current evidence. Practice-to-train conversion and its reverse also need their actual prerequisites, costs and consequences established before use; a command name is not an optimization plan.

New-character persistence deserves an explicit checkpoint. A level-one quit warning has been observed indicating that the character would not be saved. Read the current warning and return it for a decision; never repeat `quit` blindly. Reaching a saving threshold, clean disconnection and a later successful login are distinct pieces of evidence. Local broker shutdown does not guarantee an in-game save.

## A named ChatGPT interlocutor, a local Codex courier

Use one named, saved ChatGPT conversation dedicated to the project so the interlocutor can develop and revisit decisions. The browser conversation receives a deliberately selected observation packet and replies with a proposed action. A local Codex courier checks scope, ownership and freshness, then uses the local CLI or MCP adapter to carry an accepted command. The browser itself does not connect to the game or expose a direct MCP socket. This Codex workflow is a manual cross-surface relay. The separate Muse iteration provides a remote scoped adapter; it does not change this relay's ownership or protocol.

For an already-chosen command, the supplied [one-command relay helper](../scripts/relay_once.py) performs a local claim, one send and an attempted release through the actual MCP adapter. Its actor and source labels record attribution, not proof of the decision author's identity. See the [README example](../README.md#carry-one-authored-command-through-the-codex-relay) for invocation and receipt handling.

The interlocutor should choose an action for a reason: what to inspect, whether to move or wait, how to phrase an emote, or whether evidence is too weak to spend a resource. A useful proposal states one exact command or an explicit wait, its purpose, the evidence that would change the choice and a stop condition. The courier must not silently expand it into several commands or attribute a locally invented plan to the interlocutor.

Each observation packet should identify the intended actor and attributed operator without authentication material, and include:

- Observation time, connection generation, cursor range, truncation state, current owner and lease epoch.
- Exact sanitized text with its associated color information, including new ambient events.
- Relevant room, exits, occupants, objects, resources, effects and target assessment.
- The scope of the requested decision, what is uncertain and when the proposal must be reconsidered.

Keep passwords, broker tokens and lease tokens local. Review the complete outgoing packet before sharing it: email-line filtering and exact-secret masking do not guarantee that all private information was removed. Only deliberately selected material belongs in the saved conversation.

## One proposal, one lease, one send

The broker implements one current controller lease, rejects overlapping sends and permits independent observation cursors. A local human can take over; an adapter's stale lease cannot become valid merely by reusing the same actor label. These are coordination controls for trusted local participants, not proof of a remote collaborator's identity.

Before delivery, the courier rereads status and observations after the proposal's source cursor. Reconsider the proposal if an occupant arrives, combat starts, resources change materially, movement occurs, the connection generation changes, ownership changes, history is truncated, or the agreed review interval has elapsed. Set a short explicit expiry appropriate to the decision; a combat proposal should not survive a lengthy browser exchange. Do not infer that no change occurred merely because the browser reply is complete.

The Codex 0.2.0 relay forwards the original broker lifetime ID, connection generation, observation cursor and expiry. The broker checks them atomically before writing; stale or expired proposals are refused. The durable decision journal admits an ID once and preserves an uncertain outcome across restarts. These checks cover events already processed by the broker, not unseen server events or game-action completion. Legacy unguarded sends remain available, so every adapter must explicitly use the guarded interface. If freshness cannot be established, return a new observation and obtain a new decision rather than refreshing the old proposal's tuple or replaying its command.

Send the approved command once, then return the real receipt and subsequent output before selecting another command. `transport_accepted` means only that the local write/drain succeeded. A prompt, quiet period, timeout or disconnect does not certify game-action completion; `command_complete` remains false. Never replay automatically after an uncertain outcome. Inspect status and retained events, preserving uncertainty when the game's response does not settle it.

An agreed wait is also a decision: observe for its bounded interval and return changes, without treating it as permission for extra actions. Emergency responses, combat loops, scheduled play and sandbox executors require their own explicit design and authorization; none is supplied by this guide.

The result should be a decision partnership: the interlocutor interprets the world and authors a choice, the courier preserves its attribution and checks whether it is still applicable, and the client records what was actually sent and observed.
