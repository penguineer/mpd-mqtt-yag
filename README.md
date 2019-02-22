# mpt-mqtt-yag
Yet another MPD MQTT gateway.

This is inspired by https://github.com/chaosdorf/mpd-mqtt-gateway, but uses the [mpd2](https://pypi.org/project/python-mpd2/) library.

## Topics

All topics are relative to the configured topic base.

### Information topics:

* `song/file` – Song file name
* `song/artist` – Artist
* `song/album` – Album
* `song/title` – Title
* `song/track` – Track No on album
* `song/time` – Duration in Seconds

* `player/state` – Player state: play, stop or pause
* `player/elapsed` – Seconds elapsed in the current song
* `player/volume` – Replay volume (0 to 100)
* `player/repeat` – If the playlist is repeated (0 or 1)
* `player/random` – If the playlist is random (0 or 1)
* `player/single` – Single-play: stop after current song (0 or 1)

### Command topics:

* `CMD` – Send a command, where command is one of
  * `query` – Re-publish information topics
  * `play` – Start playback (Resets single-play to 0)
  * `pause` – Pause playback
  * `stop` – Stop playback (immediately)
  * `stop after` – Stop playback after current song (Sets single-play to 1)
  * `next` – Select next song
* `CMD/volume` – Set volume (0 to 100)
