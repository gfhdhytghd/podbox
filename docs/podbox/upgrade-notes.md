- **PodLink stays connected while Agents is in the background.** Switching
  to Now Playing and leaving Agents through MENU keep USB connected.
  Returning shows the cached page immediately. Disk consent on the first
  plug and pure USB Mode Charge remain available. Deploy the matching
  HerdRemote files and dependencies before using the new foreground notice
  and structured Codex detail source. Codex needs its existing app-server
  daemon; the visible-only fallback does not scroll the computer's pane.
- **Remote volume taps page once.** Consecutive dock volume steps within 300 ms form
  one press; holding past 600 ms repeats at no more than four pages/s.
  PLAY starts audio capture immediately but hides the recording band until
  the native 150 ms or remote 300 ms hold threshold.
- **Double-tap remote Play to switch between Agents and Now Playing.**
  Start the second press within 200 ms of releasing the first. A single
  tap waits for that window. In Agents, recording starts on press: remote
  taps shorter than 300 ms and native taps shorter than 150 ms discard it.
  Update the matching HerdRemote daemon for the new discard command. Menu returns
  Now Playing to Agents when the switch opened it from Agents.
- **Agents and PodLink can open with USB already connected.** A charging
  connection switches to PodLink without unplugging. A disk in use by the
  computer stays with it. USB Mode Charge keeps charging without a popup. Dock volume keys page Agents at
  up to four pages per second and leave the player's volume unchanged.
- **Remote Left/Right keep their normal tap and hold actions.** In Now
  Playing their holds seek. Menu comes from the computer's IR bridge:
  a stream continuing for at least 220 ms selects; a shorter stream sends
  Menu after 150 ms of silence. Update the matching bridge and firmware
  together. Unknown EP0 key IDs still fail.
- **Usage remote volume controls the real volume.** Returning to an Agents
  list or detail restores paging. Debug > Remote input logs volume page
  decisions even outside Agents, while retaining real volume behavior.
- **Streaming agent detail keeps existing line breaks.** Reading above the
  bottom holds the visible message; returning to the bottom adopts updates.
- **SELECT + PLAY switches between Now Playing and Agents.** From another
  screen it opens Agents. This replaces the configured Select + Play action.
  Native Play starts recording immediately. Add SELECT within 150 ms to
  discard and switch. After 150 ms, SELECT does nothing until PLAY is released.
  SELECT alone keeps its tap and held
  actions. PLAY joins the combo only before SELECT's held action fires.
- **Long comments and other tags in FLAC, Ogg, Opus, APE and WavPack files
  are now kept to 500 characters,** as in MP3 and M4A files; until now they
  were cut at about 260, or 127 in APE and WavPack. Files already in the
  library keep the short text until you run **Rebuild Database** (Settings >
  Library > Maintenance); an Update leaves them be.
- **A cover drawn only partly, its top showing and the rest flat grey,** is
  not mended by this update on its own: the art cache keeps a picture until
  its image file changes. Run **Rebuild Art Cache**, or **Check a File's Art**
  on one track of the album, to make it again. Covers are no longer kept like
  that when a read from the disk fails.
- **Select Action and Select + Play now offer the same list:** Select can run
  any of the now-playing menu's actions, Select + Play can go to Music,
  Album Covers, Files or the previous browser, and both can open Theme
  Options. Your settings are kept. Like Select + Play, Select Action is no
  longer found by Search or listed in Changed Settings, and has no Explain
  entry.
- **Menu on the now-playing screen now goes back to the list you started the
  music from** -- a Music list, Files, Playlists, Podcasts, the queue, Album
  Covers or Artist Portraits -- in the same place, and a second Menu reaches
  the main menu.
  The end of the music returns there too. To have Menu go straight to
  the main menu as before, set **Menu Action** (Settings > Playback > Now
  Playing Screen) to Main Menu. Select Action and Select + Play Action
  (Select + Play until now) offer this Previous Screen too; the choice they
  called Previous Screen before, back to the browser last used, is now
  **Previous Browser** and is still Select's default. If you had set Select +
  Play to Previous Screen, it now goes back the new way; pick Previous
  Browser to keep the old.
- **Album covers are now kept per album, not per folder,** so a library whose
  folders hold several albums each -- one copied onto the player by iTunes,
  under `iPod_Control/Music` -- shows each album its own cover in Music and
  Album Covers. The first art pass after updating moves your existing covers
  across without making them again, except where a folder held more than one
  album; those albums' covers are made afresh. The Report rebuilds its figures
  from the log once, which takes a few seconds the first time it opens.
- **The sounds of iPod click-wheel games are left out of the database,** under
  the new **Skip iPod Games** setting (Settings > Library > Database, on by
  default). On a player iTunes has synced, the next Update removes them from
  your Music lists; turn the setting off before updating to keep them.
- **Spike's Audio Offset setting is gone.** It never moved where a press was
  judged, so play is unchanged; the line it leaves in `config.cfg` is
  ignored.
