#!/usr/bin/python3

import signal
import sys

import argparse

# https://python-mpd2.readthedocs.io/en/latest/
from mpd import MPDClient

def sigint_handler(signal, frame):
    print("SIGINT received. Exit.")
    sys.exit(0)


class MpdHandler():
    def __init__(self, client):
        super().__init__()

        self.client = client


    def watch(self):
        while True:
            subsystems = self.client.idle()
            for subsystem in subsystems:
                print(subsystem)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, sigint_handler)

    parser = argparse.ArgumentParser(
        description="Yet another MPD MQTT gateway")
    parser.add_argument("--mpdhost", help="MPD host", default="localhost")
    parser.add_argument("--mpdport", help="MPD port", default=6600)
    args = parser.parse_args()

    client = MPDClient()
    client.timeout = 10
    client.idletimeout = None

    client.connect(args.mpdhost, args.mpdport)
    print("Connected to MPD {version} on {host}:{port}.".format(
        host=args.mpdhost,
        port=args.mpdport,
        version=client.mpd_version))

    handler = MpdHandler(client)
    handler.watch()
