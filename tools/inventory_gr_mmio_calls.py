#!/usr/bin/env python3
"""Conservative block-local constant inventory; never executes firmware or MMIO.

Input is an envytools envydis listing for the pinned public GV100 GR image.
Unknown calls, control-flow joins and unsupported destinations kill constants.
An unresolved argument is NOT evidence that a target cannot be accessed.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

HELPERS = {
    'fecs': {0x625: 'write', 0x6ae: 'write', 0xe02: 'read', 0xe72: 'read'},
    'gpccs': {0x196: 'write', 0x20f: 'write', 0x829: 'read', 0x88d: 'read'},
}
LISTING_HASH = {
    'fecs': '4c5cd180f69b5bc4dbd2c1c43567421ac3ea3f95dc0adc09f9448d338a71794c',
    'gpccs': 'ed2fd3615f144bfad8a136e3c2658d9f432ce211c921754b230b621a800528b2',
}
LINE = re.compile(r'^([0-9a-f]{8}):\s+((?:[0-9a-f]{2}\s+)+)(?:[BC]+\s+)?([a-z][a-z0-9]*)\s*(.*)$')
REGISTER = re.compile(r'^\$r(?:[0-9]|1[0-5])$')
READ_ONLY = {'st', 'iowr', 'cmp', 'test', 'push', 'nop', 'sleep'}


def parse(text):
    result = []
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            result.append((int(m[1], 16), m[3], m[4].split()))
        elif re.match(r'^[0-9a-f]{8}:', line):
            raise ValueError('Unparsed instruction: ' + line)
    return result


def inventory(instructions, helpers):
    targets = set()
    for pc, op, args in instructions:
        if op in {'bra', 'lbra', 'call', 'lcall'} and args:
            if args[-1].startswith('0x'):
                targets.add(int(args[-1], 16))
    regs, records = {}, []

    def value(token):
        if REGISTER.fullmatch(token):
            return regs.get(token)
        try:
            return int(token, 0)
        except ValueError:
            return None

    for pc, op, raw_args in instructions:
        if pc in targets:
            regs.clear()
        args = [x for x in raw_args if x not in {'b8', 'b16', 'b32'}]
        if op in {'call', 'lcall'}:
            target = value(args[-1]) if args else None
            if target in helpers:
                records.append({
                    'pc': hex(pc), 'helper': hex(target), 'operation': helpers[target],
                    'arguments': {f'r{i}': (hex(regs[f'$r{i}']) if f'$r{i}' in regs else None)
                                  for i in range(10, 15)},
                })
            regs.clear()  # Do not assume any interprocedural preservation.
            continue
        if op in {'bra', 'lbra', 'ret', 'exit', 'iret', 'mpopret', 'mpopaddret'}:
            regs.clear()
            continue
        if op in READ_ONLY or not args or not REGISTER.fullmatch(args[0]):
            continue
        dest, operands = args[0], args[1:]
        previous = regs.get(dest)
        regs.pop(dest, None)
        # Partial-register writes need architecture-specific upper-bit semantics.
        if 'b8' in raw_args or 'b16' in raw_args:
            continue
        val = None
        if op == 'clear':
            val = 0
        elif op == 'mov' and len(operands) == 1:
            val = previous if operands[0] == dest else value(operands[0])
        elif op in {'add', 'sub', 'and', 'or', 'xor', 'shl', 'shr'}:
            if len(operands) == 1:
                a, b = previous, value(operands[0]) if operands[0] != dest else previous
            elif len(operands) == 2:
                a, b = [previous if t == dest else value(t) for t in operands]
            else:
                continue
            if a is not None and b is not None:
                if op == 'add': val = a + b
                elif op == 'sub': val = a - b
                elif op == 'and': val = a & b
                elif op == 'or': val = a | b
                elif op == 'xor': val = a ^ b
                elif op == 'shl' and 0 <= b < 32: val = a << b
                elif op == 'shr' and 0 <= b < 32: val = a >> b
        if val is not None:
            regs[dest] = val & 0xffffffff
    return records


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('engine', choices=HELPERS)
    p.add_argument('listing', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    data = args.listing.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != LISTING_HASH[args.engine]:
        p.error('Listing hash does not match the analyzed GV100 image')
    instructions = parse(data.decode('utf-8'))
    calls = inventory(instructions, HELPERS[args.engine])
    result = {
        'schema': 'gv100-gr-block-local-mmio-inventory-v1', 'engine': args.engine,
        'listing_sha256': digest, 'instructions': len(instructions),
        'scope': 'Direct calls to four known helpers; block-local constants only. '
                 'Arguments are raw helper inputs, not normalized physical addresses. '
                 'Does not establish runtime reachability or indirect-call completeness.',
        'calls': calls,
    }
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    resolved = sum(c['arguments']['r10'] is not None for c in calls)
    print(f'{args.engine}: {len(calls)} calls; {resolved} block-local r10 values; '
          f'{len(calls)-resolved} unresolved')


if __name__ == '__main__':
    main()
