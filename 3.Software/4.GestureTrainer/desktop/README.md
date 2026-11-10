# Windows portable desktop build

The native launcher starts the bundled interpreter with no console window. `desktop.py` hosts the existing local HTTP application and a pywebview/Edge WebView2 window. Training, live prediction, and quality analysis remain isolated child processes using the same bundled interpreter. No installed Python or training venv is needed to run the product.

Build on Windows with verified Python 3.13 x64 training dependencies (`../requirements.txt`) and a separate Python 3.13 x64 GUI environment (`requirements.txt`). Microsoft's .NET Framework C# compiler builds the small launcher. The builder copies the complete numerical libraries, their license files, the Python runtime, UI; no firmware template or compiler is included. Do not exclude package directories named `test`: TensorFlow imports APIs with that name.

```text
python build_windows.py --training-python <training-venv>/Scripts/python.exe --window-python <GUI-venv>/Scripts/python.exe --output <new-product-folder> --seed <official-example-data> --license <project-LICENSE> --zip
```

Use a fresh output folder. User data, trained models, device backups and build caches must never be inputs to the distribution. Official example data supports the legacy example import only; the gesture workflow starts empty on a new computer.

The portable `_pth` file isolates imports from installed Python and includes the application directory for worker imports. Its `sitecustomize.py` keeps JSON worker streams in UTF-8. The native launcher also clears installed-Python environment overrides. WebView2 uses a per-user cache; the supplied Microsoft bootstrapper can install the display component if it is absent.

Validate the native window with `魔杖手势训练.exe --workspace <isolated-folder> --smoke-output <result.json>`. This opens the real Windows window, waits for the four-stage application to initialize, records its state and closes. Separately run actual training/export/prediction using the bundled `runtime/python.exe` with installed Python excluded from PATH. System firmware is maintained separately on the wand. Never flash test artifacts.

Source tests: `python -m unittest discover -s tests -p test_*.py`.

The builder applies the explicit policy in `slim_windows.py`: omit C++ headers,
native import/debug libraries, cached bytecode, libclang development tooling and
the legacy PlatformIO module. Keep runtime DLLs, TensorFlow public Python APIs
(including names containing `test`), training/export/prediction dependencies and
third-party license metadata. This runtime supports this application, rather than
general Python extension development. Validate actual training, INT8 export and
prediction, data review and the native WebView2 window for each build.

Windows NTFS compression may additionally reduce allocated disk space without
changing file contents. It is a local deployment option, not part of the ZIP
format; measure logical and allocated sizes separately and verify after applying.
