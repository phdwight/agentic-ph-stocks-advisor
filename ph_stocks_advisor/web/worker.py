"""
Custom Gunicorn gevent worker that skips SSL monkey-patching.

Single Responsibility: this module only provides a safe gevent worker
class for Gunicorn.  The standard ``gevent.monkey.patch_all()`` patches
Python's :mod:`ssl` module, which causes infinite recursion in the
``ssl.SSLContext.minimum_version`` property setter on Python 3.12 with
OpenSSL ≥ 3.5.  Skipping the SSL patch is safe because:

* TLS termination happens at the Azure Container Apps ingress (or
  reverse proxy), not inside our application server.
* Outbound HTTPS (e.g. OAuth token exchange, API calls) works
  correctly with the **unpatched** :mod:`ssl` module — only the
  *gevent-patched* version triggers the recursion.
"""

from __future__ import annotations

from gunicorn.workers.ggevent import GeventWorker


class GeventWorkerNoSSL(GeventWorker):
    """GeventWorker that monkey-patches everything *except* ``ssl``."""

    def patch(self) -> None:  # type: ignore[override]
        from gevent import monkey

        monkey.noisy = False  # type: ignore[attr-defined]
        monkey.patch_all(ssl=False)
