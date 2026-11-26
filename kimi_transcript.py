"""Kimi Code 2.5.3 transcript protocol (not the persisted wire format).

Fields verified against packages/transcript schema and kap-server routes in
the 2.5.3 executable. Only main-agent, completed, complete text is publishable.
"""

import json
from copy import deepcopy


class Transcript:
    def __init__(self):
        self.items = {}
        self.seq = None
        self.incomplete = False

    def reset(self, snapshot, seq=None):
        self.items = {item['turnId']: deepcopy(item) for item in snapshot.get('items', [])
                      if item.get('kind') == 'turn'}
        self.seq = seq
        self.incomplete = False

    def apply(self, ops, seq):
        previous_items, previous_seq = deepcopy(self.items), self.seq
        previous_incomplete = self.incomplete
        self.incomplete = False
        applied = self._apply_ops(ops, seq)
        if self.incomplete:
            self.items, self.seq = previous_items, previous_seq
            return False
        if not applied:
            self.incomplete = previous_incomplete
        return applied

    def _apply_ops(self, ops, seq):
        if self.seq is not None and seq is not None:
            if seq <= self.seq:
                return False
            if seq != self.seq + 1:
                self.incomplete = True
                return False
        for op in ops:
            if self.incomplete:
                return False
            kind = op.get('op')
            if kind == 'reset':
                self.reset(op['snapshot'])
            elif kind == 'turn.upsert':
                turn = op['turn']
                old = self.items.get(turn['turnId'], {})
                self.items[turn['turnId']] = {**old, **deepcopy(turn), 'steps': old.get('steps', [])}
            elif kind in {'step.upsert', 'frame.upsert', 'append'}:
                target = op.get('target', op)
                turn = self.items.get(target.get('turnId'))
                if turn is None:
                    self.incomplete = True
                    continue
                steps = turn.setdefault('steps', [])
                step_id = op.get('step', {}).get('stepId') if kind == 'step.upsert' else target.get('stepId')
                step = next((s for s in steps if s['stepId'] == step_id), None)
                if kind == 'step.upsert':
                    updated = {**(step or {}), **deepcopy(op['step']), 'frames': (step or {}).get('frames', [])}
                    if step is None:
                        steps.append(updated)
                    else:
                        steps[steps.index(step)] = updated
                elif step is None:
                    self.incomplete = True
                elif kind == 'frame.upsert':
                    frames = step.setdefault('frames', [])
                    frame = next((f for f in frames if f['frameId'] == op['frame']['frameId']), None)
                    if frame is None:
                        frames.append(deepcopy(op['frame']))
                    else:
                        frames[frames.index(frame)] = deepcopy(op['frame'])
                else:
                    frame = next((f for f in step.get('frames', []) if f['frameId'] == target.get('frameId')), None)
                    if frame is None:
                        self.incomplete = True
                        continue
                    # JS producer offsets count UTF-16 code units. Work on
                    # those units, including overlaps across surrogate pairs.
                    text = frame.get('text', '').encode('utf-16-le', errors='surrogatepass')
                    offset = op['offset'] * 2
                    fragment = op['text'].encode('utf-16-le', errors='surrogatepass')
                    if offset > len(text):
                        self.incomplete = True
                        continue
                    overlap = min(len(text) - offset, len(fragment))
                    if text[offset:offset + overlap] != fragment[:overlap]:
                        self.incomplete = True
                    else:
                        frame['text'] = (text + fragment[overlap:]).decode('utf-16-le', errors='surrogatepass')
            elif kind == 'items.remove':
                for item_id in op.get('ids', []):
                    self.items.pop(item_id, None)
        if self.incomplete:
            return False
        self.seq = seq
        return True

    def results(self):
        if self.incomplete:
            return []
        results = []
        for turn in self.items.values():
            prompt = turn.get('triggerPromptId')
            if not prompt or turn.get('state') not in {'completed', 'failed', 'cancelled'}:
                continue
            if turn['state'] != 'completed':
                results.append((turn, ''))
                continue
            text = ''.join(frame.get('text', '') for frame in _final_assistant_frames(turn))
            if text.strip():
                results.append((turn, text))
        return results


def _final_assistant_frames(turn):
    if turn.get('state') != 'completed':
        return []
    steps = sorted(turn.get('steps', []), key=lambda step: step.get('ordinal', 0))
    # The last model step is distinct from tool-confirmed intermediate commentary.
    if not steps:
        return []
    last = steps[-1]
    steered = last.get('state') == 'interrupted' and last.get('endReason') == 'steered'
    if last.get('state') != 'completed' and not steered:
        return []
    frames = last.get('frames', [])
    if any(frame.get('kind') == 'tool' for frame in frames):
        return []
    if steered:
        boundary = max((i for i, frame in enumerate(frames)
                        if frame.get('kind') == 'text' and frame.get('role') == 'user'), default=-1)
        frames = frames[boundary + 1:]
    return [frame for frame in frames
            if frame.get('kind') == 'text' and frame.get('role') == 'assistant'
            and not any(frame.get(key) for key in ('private', 'hidden', 'non_disclosable'))
            and frame.get('disclosable', True) is not False]


def final_message_identity(turn):
    # A public final can consist of multiple text frames in the same model step.
    frame_ids = [frame.get('frameId') for frame in _final_assistant_frames(turn) if frame.get('text')]
    if not frame_ids or not all(isinstance(value, str) and value.strip() for value in frame_ids):
        return ""
    return json.dumps(frame_ids, ensure_ascii=False, separators=(",", ":"))
