"""Kimi Code 2.5.3 transcript protocol (not the persisted wire format).

Fields verified against packages/transcript schema and kap-server routes in
the 2.5.3 executable. Only main-agent, completed, complete text is publishable.
"""
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
            steps = sorted(turn.get('steps', []), key=lambda s: s.get('ordinal', 0))
            # The final model step is distinct from tool-confirmed intermediate
            # assistant commentary. Never publish an earlier assistant frame.
            if not steps:
                continue
            last = steps[-1]
            steered = last.get('state') == 'interrupted' and last.get('endReason') == 'steered'
            if last.get('state') != 'completed' and not steered:
                continue
            frames = last.get('frames', [])
            if any(f.get('kind') == 'tool' for f in frames):
                continue
            if steered:
                # A steer can retain pre-follow-up partial output in this step.
                # Only structured user frames delimit the final response.
                boundary = max((i for i, frame in enumerate(frames)
                                if frame.get('kind') == 'text' and frame.get('role') == 'user'), default=-1)
                frames = frames[boundary + 1:]
            texts = [f.get('text', '') for f in frames
                     if f.get('kind') == 'text' and f.get('role') == 'assistant'
                     and not any(f.get(k) for k in ('private', 'hidden', 'non_disclosable'))
                     and f.get('disclosable', True) is not False]
            text = ''.join(texts)
            if turn['state'] == 'completed' and not text.strip():
                continue
            results.append((turn, text))
        return results
