#!/usr/bin/env python3
"""WSL-only serial console; save every exported recording as a JSON artifact."""
import argparse
import datetime
import json
from pathlib import Path
import time

import serial
from serial.tools import list_ports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', help='e.g. /dev/ttyACM0; auto-select a single Espressif USB device')
    parser.add_argument('--command', default='status')
    parser.add_argument('--seconds', type=float, default=4)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'captures')
    args = parser.parse_args()
    if not args.port:
        ports = [p.device for p in list_ports.comports() if p.vid == 0x303A]
        if len(ports) != 1:
            parser.error(f'Expected one Espressif USB device, found {ports}; pass --port')
        args.port = ports[0]
    if not args.port.startswith('/dev/'):
        parser.error('Use a WSL /dev/ serial port; Windows COM ports are not supported')
    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    log_path = args.output / f'{stamp}.log'
    with serial.Serial(args.port, 115200, timeout=0.2) as device, log_path.open('w') as log:
        # Opening native USB CDC must not assert a UART reset sequence.
        time.sleep(1.5)
        device.write((args.command + '\n').encode())
        end = time.monotonic() + args.seconds
        pending = bytearray()
        exported = 0
        while time.monotonic() < end:
            pending.extend(device.read(max(1, device.in_waiting)))
            while b'\n' in pending:
                raw, _, remainder = pending.partition(b'\n')
                pending = bytearray(remainder)
                line = raw.decode('utf-8', errors='replace').rstrip('\r')
                print(line, flush=True)
                log.write(line + '\n')
                log.flush()
                if line.startswith('{'):
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get('type') == 'recording':
                        exported += 1
                        path = args.output / f'{stamp}-{exported}.json'
                        path.write_text(json.dumps(data, indent=2) + '\n')
                        print(f'Saved: {path}', flush=True)
    print(f'Log: {log_path}')


if __name__ == '__main__':
    main()
