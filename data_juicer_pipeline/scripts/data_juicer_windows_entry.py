"""Windows single-process compatibility entry for Data-Juicer 1.5.3.

Hugging Face datasets treats ``num_proc=1`` as a spawned worker pool.  In the
installed Windows environment that worker dies while re-importing the CLI.
Normalizing one worker to the library's true single-process value (None)
keeps all Data-Juicer operator semantics unchanged.
"""

from __future__ import annotations

import os

if os.name == "nt":
    from datasets.arrow_dataset import Dataset

    _original_map = Dataset.map
    _original_cleanup = Dataset.cleanup_cache_files

    def _single_process_map(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("num_proc") == 1:
            kwargs["num_proc"] = None
        return _original_map(self, *args, **kwargs)

    Dataset.map = _single_process_map  # type: ignore[method-assign]

    def _windows_safe_cleanup(self):  # type: ignore[no-untyped-def]
        try:
            return _original_cleanup(self)
        except PermissionError:
            # Arrow may still hold the memory map on Windows. The temporary
            # directory finalizer retries after the dataset is released.
            return 0

    Dataset.cleanup_cache_files = _windows_safe_cleanup  # type: ignore[method-assign]

from data_juicer.tools.process_data import main


if __name__ == "__main__":
    main()
