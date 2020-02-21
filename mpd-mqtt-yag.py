#!/usr/bin/python3

import signal
import sys
import time

import argparse

# https://python-mpd2.readthedocs.io/en/latest/
import mpd
from mpd import MPDClient

import paho.mqtt.client as mqtt

from collections import deque


MQTT_TOPICS = {}


def mqtt_add_topic_callback(mqtt_client, topic, cb):
    MQTT_TOPICS[topic] = cb

    mqtt_client.subscribe(topic)
    mqtt_client.message_callback_add(topic, cb)


def on_mqtt_connect(client, _userdata, _flags, rc):
    print("MQTT connected with code %s" % rc)
    for topic, cb in MQTT_TOPICS.items():
        client.subscribe(topic)
        client.message_callback_add(topic, cb)


def sigint_handler(_signal, _frame):
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


class MpdClientPool:
    def __init__(self, host, port, password=None):
        self.host = host
        self.port = port
        self.password = password

        self.clients = deque()

    def _create_client(self):
        client = MPDClient()
        client.timeout = 10
        client.idletimeout = None
        if self.password:
            client.password(self.password)

        client.connect(self.host, self.port)

        return client

    def acquire(self):
        client = None

        tries = 10
        timeout = 5

        while not client and tries:
            try:
                if self.clients:
                    print("Create a new MPD client.")
                    client = self.clients.pop()
                else:
                    print("Reuse an MPD client.")
                    client = self._create_client()

                client.ping()

            except Exception as e:
                print("Client connection error: {} {}".format(e.__class__.__name__, str(e)))
                try:
                    client.disconnect()
                except mpd.ConnectionError as e:
                    print("Error during disconnect: {} {}".format(e.__class__.__name__, str(e)))

                client = None

                if not self.clients:
                    if tries == 0:
                        raise
                    tries = tries - 1
                    time.sleep(timeout)

        print("Acquired an MPD client, queue size is {}".format(len(self.clients)))

        return client

    def drop(self, client):
        self.clients.append(client)

        print("Returned an MPD client, queue size is {}".format(len(self.clients)))


class MpdHandler:
    def __init__(self, mpd_pool, fav_tag=None, fav_needle=None):
        self.mpd_pool = mpd_pool
        self.song_cb = None
        self.play_cb = None
        self.elapsed_cb = None
        self.volume_cb = None
        self.repeat_random_cb = None
        self.single_cb = None

        self.fav_tag = fav_tag
        self.fav_needle = fav_needle

        self.status = ObservedDict()
        self.song = ObservedDict()

    def set_callback(self,
                     song_cb=None,
                     play_cb=None,
                     elapsed_cb=None,
                     volume_cb=None,
                     repeat_random_cb=None,
                     single_cb=None):
        self.song_cb = song_cb
        self.play_cb = play_cb
        self.elapsed_cb = elapsed_cb
        self.volume_cb = volume_cb
        self.repeat_random_cb = repeat_random_cb
        self.single_cb = single_cb

    def cmd_play(self):
        mpd_client = self.mpd_pool.acquire()
        mpd_client.single(0)
        mpd_client.play()
        self.mpd_pool.drop(mpd_client)

    def cmd_pause(self):
        mpd_client = self.mpd_pool.acquire()
        mpd_client.pause()
        self.mpd_pool.drop(mpd_client)

    def cmd_stop(self):
        mpd_client = self.mpd_pool.acquire()
        mpd_client.stop()
        self.mpd_pool.drop(mpd_client)

    def cmd_stop_after(self):
        mpd_client = self.mpd_pool.acquire()
        mpd_client.single(1)
        self.mpd_pool.drop(mpd_client)

    def cmd_next(self):
        mpd_client = self.mpd_pool.acquire()
        mpd_client.next()
        self.mpd_pool.drop(mpd_client)

    def cmd_volume(self, volume):
        try:
            vol = int(volume)

            mpd_client = self.mpd_pool.acquire()
            mpd_client.setvol(vol)
            self.mpd_pool.drop(mpd_client)
        except ValueError as e:
            print(e)

    def cmd_fav(self):
        if (self.fav_tag is None) or (self.fav_needle is None):
            return

        mpd_client = self.mpd_pool.acquire()
        res = mpd_client.playlistfind(self.fav_tag, self.fav_needle)
        if res and ('id' in res[0]):
            song_id = res[0]['id']
            mpd_client.playid(song_id)
        self.mpd_pool.drop(mpd_client)

    def emit_song(self):
        if self.song_cb is None:
            return

        keys = ['album', 'artist', 'file', 'time', 'title', 'track']
        song = {k: v for k, v in filter(lambda i: i[0] in keys, self.song.items())}

        self.song_cb(song)

    def emit_state(self):
        state = self.status['state']
        if self.play_cb is not None:
            self.play_cb(state)

    def emit_elapsed(self):
        elapsed = self.status['elapsed']
        if self.elapsed_cb is not None:
            self.elapsed_cb(elapsed)

    def emit_volume(self):
        volume = self.status['volume']
        if self.volume_cb is not None:
            self.volume_cb(volume)

    def emit_random_repeat(self):
        repeat = self.status['repeat']
        random = self.status['random']
        if self.repeat_random_cb is not None:
            self.repeat_random_cb(repeat, random)

    def emit_single(self):
        single = self.status['single']
        if self.single_cb is not None:
            self.single_cb(single)

    def watch(self):
        subsystems = []

        while True:
            self._check_updates(subsystems)

            mpd_client = self.mpd_pool.acquire()
            try:
                subsystems = mpd_client.idle()
            except Exception as e:
                print("Exception {} during MPD idle: {}".format(e.__class__.__name__, str(e)))
            self.mpd_pool.drop(mpd_client)

    def _check_updates(self, subsystems=None):
        self._update_status()
        status_changes = self.status.get_changes()
        self._update_song()
        song_changes = self.song.get_changes()

        if status_changes or song_changes:
            self._dispatch_change_events(subsystems, status_changes, song_changes)

    def _update_status(self):
        mpd_client = self.mpd_pool.acquire()
        st_py = mpd_client.status()
        self.mpd_pool.drop(mpd_client)
        for name, val in st_py.items():
            self.status[name] = val

    def _update_song(self):
        mpd_client = self.mpd_pool.acquire()
        song_py = mpd_client.currentsong()
        self.mpd_pool.drop(mpd_client)
        for name, val in song_py.items():
            self.song[name] = val

    def _dispatch_change_events(self, _subsystems, status_changes, song_changes):
        if any(e in ['artist', 'title', 'album'] for e in song_changes):
            self.emit_song()

        if 'state' in status_changes:
            self.emit_state()

        if 'elapsed' in status_changes:
            self.emit_elapsed()

        if 'volume' in status_changes:
            self.emit_volume()

        if any(e in ['repeat', 'random'] for e in status_changes):
            self.emit_random_repeat()

        if 'single' in status_changes:
            self.emit_single()


class MqttHandler:
    def __init__(self, mqtt_client, topic_base, mpd_client):
        self.mqtt_client = mqtt_client
        self.topic_base = topic_base

        self.mpd = mpd_client
        self.mpd.set_callback(song_cb=self.song_cb,
                              play_cb=self.play_cb,
                              elapsed_cb=self.elapsed_cb,
                              volume_cb=self.volume_cb,
                              repeat_random_cb=self.repeat_random_cb,
                              single_cb=self.single_cb)

        mqtt_add_topic_callback(mqtt_client, self._render_topic("CMD"), self._dispatch_command_mqtt_cb)
        mqtt_add_topic_callback(mqtt_client, self._render_topic("CMD/volume"), self._volume_mqtt_cb)

    def song_cb(self, song):
        for key, value in song.items():
            self.mqtt_client.publish(self._render_topic("song/" + key), value, qos=2)

    def play_cb(self, state):
        self.mqtt_client.publish(self._render_topic("player/state"), state, qos=2)

    def elapsed_cb(self, elapsed):
        self.mqtt_client.publish(self._render_topic("player/elapsed"), elapsed, qos=2)

    def volume_cb(self, volume):
        self.mqtt_client.publish(self._render_topic("player/volume"), volume, qos=2)

    def repeat_random_cb(self, repeat, random):
        self.mqtt_client.publish(self._render_topic("player/repeat"), repeat, qos=2)
        self.mqtt_client.publish(self._render_topic("player/random"), random, qos=2)

    def single_cb(self, single):
        self.mqtt_client.publish(self._render_topic("player/single"), single, qos=2)

    def _render_topic(self, rel):
        delim = "" if self.topic_base.endswith("/") or rel.startswith("/") else "/"
        return "{base}{delim}{rel}".format(base=self.topic_base, delim=delim, rel=rel)

    def _dispatch_command_mqtt_cb(self, _client, _userdata, message):
        cmd = message.payload.decode("utf-8")

        commands = {
            'query': self._cmd_query,
            'play': self.mpd.cmd_play,
            'pause': self.mpd.cmd_pause,
            'stop': self.mpd.cmd_stop,
            'stop after': self.mpd.cmd_stop_after,
            'next': self.mpd.cmd_next,
            'fav': self.mpd.cmd_fav
            }

        if cmd in commands:
            commands[cmd]()

    def _volume_mqtt_cb(self, _client, _userdata, message):
        volume = message.payload.decode("utf-8")

        self.mpd.cmd_volume(volume)

    def _cmd_query(self):
        if self.mpd is not None:
            self.mpd.emit_song()
            self.mpd.emit_state()
            self.mpd.emit_elapsed()
            self.mpd.emit_volume()
            self.mpd.emit_random_repeat()
            self.mpd.emit_single()


def main():
    signal.signal(signal.SIGINT, sigint_handler)

    parser = argparse.ArgumentParser(
        description="Yet another MPD MQTT gateway")
    parser.add_argument("--mpdhost", help="MPD host", default="localhost")
    parser.add_argument("--mpdport", help="MPD port", default=6600)
    parser.add_argument("--mqtthost", help="MQTT host", default="localhost")
    parser.add_argument("--mqttport", help="MQTT port", default=1883)
    parser.add_argument("--topic", help="MQTT topic prefix", default="MPD")
    parser.add_argument("--favtag", help="Favourite song tag", default="title")
    parser.add_argument("--favneedle", help="Favourite song query", default=None)
    args = parser.parse_args()

    mqttclient = mqtt.Client()
    mqttclient.on_connect = on_mqtt_connect
    mqttclient.connect(args.mqtthost, args.mqttport, 60)
    mqttclient.loop_start()

    mpd_pool = MpdClientPool(args.mpdhost, args.mpdport)

    mpd_ver = mpd_pool.acquire()
    print("Connected to MPD {version} on {host}:{port}.".format(
        host=args.mpdhost,
        port=args.mpdport,
        version=mpd_ver.mpd_version))

    # disable playlist consumption
    mpd_ver.consume(0)
    mpd_pool.drop(mpd_ver)

    handler = MpdHandler(mpd_pool,
                         fav_tag=args.favtag,
                         fav_needle=args.favneedle)

    MqttHandler(mqttclient, args.topic, handler)

    handler.watch()

    mqttclient.loop_stop()


if __name__ == "__main__":
    main()
