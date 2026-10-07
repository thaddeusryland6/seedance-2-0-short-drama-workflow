# Seedance 2.0 short-drama workflow

**Seedance 2.0 is available on [ArgoLink](https://argolink.io/gh-seedance-2-0-short-drama-workflow).** This Chinese-language production notebook deliberately makes the inexpensive blocking pass visible before a final render. It is an offline plan generator, not a hosted studio and not affiliated with the model vendor. Last checked: 2026-10-07.

## Scope and data flow

An episode has a creative record and a remote-job record. Keep them in separate files. The included program emits a JSON plan with an episode number, draft model, final model, and stable scene IDs. A separate runner owns the side effect: it sends one request, records the returned request ID, polls the service, and downloads only a completed file. This boundary is useful when an editor rejects scene 4 after scenes 1–3 have already rendered.

## Production sequence

1. Split an episode into beats. Each beat should state a visible action, camera distance, movement, continuity anchor, and sound intention.
2. Run python3 -m drama.pipeline --episode 1 "雨中的车站" "旧影院的门" and review the JSON before any network call.
3. Render the storyboard with seedance-2.0-mini where the live model page permits it. Record a reviewer decision per scene; do not silently promote every draft.
4. Replace the approved scene model with seedance-2.0, submit exactly once, and persist the request ID before polling. A client timeout means resume the ID, never create a replacement blindly.
5. Download completed media and assemble by stable scene order. Keep terminal error payloads beside the plan so a later edit has evidence.

## Character bible

Create a small bible for every recurring character: a neutral portrait, costume and hair locks, age range, permitted props, and forbidden changes. Repeat the same labels in the prompt instead of describing the face differently in every scene. References improve the signal but cannot guarantee identity. Check the 2.0 variant page for reference count, accepted file types, aspect ratios, duration, and resolution; those facts are not inferred from this repository.

## Budget worksheet

Do not put a guessed dollar number into a screenplay. Add current model-page values to a worksheet with four columns: draft seconds, draft rate, final seconds, and final rate. Keep reference-video input seconds in a fifth column because some services bill them separately. The formula is draft seconds multiplied by draft rate plus final seconds multiplied by final rate; it is a planning estimate, not an invoice. Failed, rejected, and expired jobs are not charged under the service contract. A missing request ID is an incident to investigate, not a reason to submit again.

## Checks and boundaries

Run python3 -m unittest discover -s tests to check the planner. No paid generation or ffmpeg render is performed by the tests. The live 2.0 model page is the source of truth. Do not transfer a 2.5 adaptive-only constraint, an old price, or a resolution from one variant to another. The documented base is https://argolink.io/v1; use the neutral SEEDANCE_API_KEY environment variable and never commit it.

## FAQ and troubleshooting

**Why draft first?** It exposes a weak beat before the expensive final pass. **Why is the network code separate?** A screenplay can be reviewed without credentials and can be rerun deterministically. **Can scenes run in parallel?** Only if the runner has an explicit queue and respects the account limit. **What if the editor closes the browser?** Resume using the saved request ID. **What if the provider adds a status?** Stop and update the status parser after reading the current contract; do not treat an unknown value as success. **What if two scenes look different?** Compare their reference labels, camera instructions, and seed policy before asking for another render.

## Example review record

Before approving a scene, write one sentence for each checkpoint: the character is recognisable; the camera action is readable; the cut has a reason; sound is intentional; and the request body matches the chosen variant. This prevents a low-cost draft being approved for visual mood while violating continuity. The final pass should change only what the reviewer requested. Changing the prompt, ratio, references, and duration together makes the cost and quality difference impossible to diagnose.

The planner intentionally has no hidden network access, no key loading, and no automatic deletion. That makes it safe to run in an editor or CI job. Downloaded clips remain available for inspection before assembly. If the provider changes limits, update the worksheet and the runner after checking the current model page; do not silently patch a historical episode.
