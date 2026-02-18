python -m nuitka --standalone ^
--show-progress ^
--plugin-enable=pyqt5 ^
--windows-disable-console ^
--include-data-files=src/browsers.jsonl=fake_useragent/data/browsers.jsonl ^
--include-data-files=src/downloader.ico=src/downloader.ico ^
--windows-icon-from-ico=src/downloader.ico ^
--output-dir=build ^
main.py