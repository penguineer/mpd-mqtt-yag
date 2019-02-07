#!/usr/bin/python3

import signal
import sys

import argparse

# https://python-mpd2.readthedocs.io/en/latest/
from mpd import MPDClient

import paho.mqtt.client as mqtt


MQTT_TOPICS = {}

def mqtt_add_topic_callback(mqtt, topic, cb):
    MQTT_TOPICS[topic] = cb

    mqtt.subscribe(topic)
    mqtt.message_callback_add(topic, cb)


def on_mqtt_connect(client, userdata, flags, rc):
    print("MQTT connected with code %s" % rc)
    for topic, cb in MQTT_TOPICS.items():
        client.subscribe(topic)
        client.message_callback_add(topic, cb)


def sigint_handler(signal, frame):
    print("SIGINT received. Exit.")
    sys.exit(0)


class ObservedDict(dict):
    def __init__(self, **kwargs):
        super(ObservedDict, self).__init__(**kwargs)

        self.changes = {}


    def __setitem__(self, item, value):
        oldval = self.__getitem__(item) if item in self else None

        if not oldval or oldval != value:
            self.changes[item] = value

        super(ObservedDict, self).__setitem__(item, value)


    def get_changes(self):
        ch = self.changes
        self.changes = {}
        return ch


class MpdHandler():
    def __init__(self, mpd):
        self.mpd = mpd
        self.status = ObservedDict()
        self.song = ObservedDict()


    def watch(self):
        self._check_updates()

        while True:
            subsystems = self.mpd.idle()
            self._check_updates(subsystems)


    def _check_updates(self, subsystems=None):
        self._update_status()
        status_changes = self.status.get_changes()
        self._update_song()
        song_changes = self.song.get_changes()

        if status_changes or song_changes:
            self._dispatch_change_events(subsystems, status_changes, song_changes)


    def _update_status(self):
        st_py = self.mpd.status()
        for name, val in st_py.items():
            self.status[name] = val


    def _update_song(self):
        song_py = self.mpd.currentsong()
        for name, val in song_py.items():
            self.song[name] = val


    def _dispatch_change_events(self, subsystems, status_changes, song_changes):
        if any (e in ['artist', 'title', 'album'] for e in song_changes):
            keys = ['album', 'artist', 'date', 'file', 'time', 'title', 'track']
            song = {k: v for k, v in filter(lambda i: i[0] in keys, self.song.items())}
            print("song changed to %s" % str(song))

        if 'state' in status_changes:
            state = self.status['state']
            print("player state changed to %s" % state)

        if 'elapsed' in status_changes:
            elapsed = self.status['elapsed']
            print("elapsed changed to %s" % elapsed)

        if 'volume' in status_changes:
            volume = self.status['volume']
            print("volume changed to %s" % volume)

        if any (e in ['repeat', 'random'] for e in status_changes):
            repeat = self.status['repeat']
            random = self.status['random']
            print("play mode changed to repeat=%s, random=%s" % (repeat, random))

        if 'single' in status_changes:
            single = self.status['single']
            print("single play mode changed to %s" % single)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, sigint_handler)

    parser = argparse.ArgumentParser(
        description="Yet another MPD MQTT gateway")
    parser.add_argument("--mpdhost", help="MPD host", default="localhost")
    parser.add_argument("--mpdport", help="MPD port", default=6600)
    parser.add_argument("--mqtthost", help="MQTT host", default="localhost")
    parser.add_argument("--mqttport", help="MQTT port", default=1883)
    parser.add_argument("--topic", help="MQTT topic prefix", default="MPD")
    args = parser.parse_args()

    mqttclient = mqtt.Client()
    mqttclient.on_connect = on_mqtt_connect
    mqttclient.connect(args.mqtthost, args.mqttport, 60)
    mqttclient.loop_start()

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

    mqttclient.loop_stop()
