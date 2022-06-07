.PHONY: release run

release: start.py backend/kongdl.py backend/checkpoint.py backend/debugLib.py
	pyinstaller -F start.py
	cp settings.txt dist/
	zip -j linux64.zip dist/*
	rm -r dist build start.spec

run:
	python3 start.py
