"""Host default-sink volume. No shell, sudo, or Cider volume RPC."""
import re
import shutil
import subprocess


class SystemVolume:
    def __init__(self, run=subprocess.run, which=shutil.which):
        self.run = run
        self.wpctl = which('wpctl')
        self.pactl = which('pactl') if not self.wpctl else None
        self.volume, self.muted = 0, False

    def _call(self, args):
        try:
            result = self.run(args, capture_output=True, text=True, timeout=.6,
                              check=True)
            return result.stdout
        except (subprocess.SubprocessError, OSError) as error:
            raise OSError('System volume unavailable') from error

    def read(self):
        if self.wpctl:
            text = self._call([self.wpctl, 'get-volume', '@DEFAULT_AUDIO_SINK@'])
            match = re.search(r'Volume:\s*([0-9.]+)', text)
            if not match:
                raise OSError('Invalid wpctl volume')
            self.volume = round(float(match[1]) * 100)
            self.muted = '[MUTED]' in text
        elif self.pactl:
            text = self._call([self.pactl, 'get-sink-volume', '@DEFAULT_SINK@'])
            match = re.search(r'(\d+)%', text)
            if not match:
                raise OSError('Invalid pactl volume')
            self.volume = int(match[1])
            self.muted = 'yes' in self._call([self.pactl, 'get-sink-mute', '@DEFAULT_SINK@']).lower()
        else:
            raise OSError('wpctl/pactl missing')
        return min(100, max(0, self.volume)), self.muted

    def step(self, delta):
        delta = max(-100, min(100, int(delta)))
        if not delta:
            return
        if self.wpctl:
            self._call([self.wpctl, 'set-volume', '-l', '1.0',
                        '@DEFAULT_AUDIO_SINK@', str(abs(delta)) + ('%+' if delta > 0 else '%-')])
        elif self.pactl:
            volume, _ = self.read()
            self._call([self.pactl, 'set-sink-volume', '@DEFAULT_SINK@',
                        str(max(0, min(100, volume + delta))) + '%'])
        else:
            raise OSError('wpctl/pactl missing')
