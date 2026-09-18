"""Explicit provisioning step. Never invoked while processing campaigns/tests."""
import os
from pathlib import Path

os.environ.setdefault('U2NET_HOME', str(Path('.cache/models').resolve()))

from rembg.sessions.u2netp import U2netpSession

if __name__ == '__main__':
    print(U2netpSession.download_models())
