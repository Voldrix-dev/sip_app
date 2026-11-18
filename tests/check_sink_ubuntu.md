ubuntu 22.04.5 LTS

pcuser@pcuser-desktop:~$ pactl list sink-inputs 
Вход аудиоприёмника №3
	Драйвер: protocol-native.c
	Модуль-владелец: 11
	Клиент: 28
	Аудиоприёмник: 0
	Спецификация отсчётов: s16le 1-канальный 4800
	Схема каналов: mono
	Формат: pcm, format.sample_format = "\"s16le\""  format.rate = "48000"  format.channels = "1"  format.channel_map = "\"mono\""
	Поток данных приостановлен: нет
	Звук выключен: нет
	Громкость: mono: 65536 / 100% / 0,00 dB
	        баланс 0,00
	Задержка буфера: 187500 мкс
	Задержка аудиоприёмника: 2863 мкс
	Метод передискретизации: copy
	Свойства:
		media.name = "ALSA Playback"
		application.name = "ALSA plug-in [python3.14]"
		native-protocol.peer = "UNIX socket client"
		native-protocol.version = "35"
		application.process.id = "3957"
		application.process.user = "pcuser"
		application.process.host = "pcuser-desktop"
		application.process.binary = "python3.14"
		application.language = "ru_RU.UTF-8"
		window.x11.display = ":0"
		application.process.machine_id = "211d6c3d504645f1ae2d1a729f952ce3"
		application.icon_name = "/usr/share/pixmaps/python3.14.xpm"
		module-stream-restore.id = "sink-input-by-application-name:ALSA plug-in [python3.14]"

Вход аудиоприёмника №4
	Драйвер: protocol-native.c
	Модуль-владелец: 11
	Клиент: 30
	Аудиоприёмник: 1
	Спецификация отсчётов: float32le 2-канальный 
	Схема каналов: front-left,front-right
	Формат: pcm, format.sample_format = "\"float32le\""  format.rate = "44100"  format.channels = "2"  format.channel_map = "\"front-left,front-right\""
	Поток данных приостановлен: нет
	Звук выключен: нет
	Громкость: front-left: 65536 / 100% / 0,00 dB,   front-right: 65536 / 100% / 0,00 dB
	        баланс 0,00
	Задержка буфера: 125162 мкс
	Задержка аудиоприёмника: 8603 мкс
	Метод передискретизации: speex-float-1
	Свойства:
		media.name = "Radio Garden – Electric FM"
		application.name = "Firefox"
		native-protocol.peer = "UNIX socket client"
		native-protocol.version = "35"
		application.process.id = "2315"
		application.process.user = "pcuser"
		application.process.host = "pcuser-desktop"
		application.process.binary = "firefox"
		application.language = "ru_RU.UTF-8"
		window.x11.display = ":0"
		application.process.machine_id = "211d6c3d504645f1ae2d1a729f952ce3"
		application.icon_name = "firefox"
		module-stream-restore.id = "sink-input-by-application-name:Firefox"

Вход аудиоприёмника №7
	Драйвер: protocol-native.c
	Модуль-владелец: 11
	Клиент: 33
	Аудиоприёмник: 0
	Спецификация отсчётов: s16le 1-канальный 4410
	Схема каналов: mono
	Формат: pcm, format.sample_format = "\"s16le\""  format.rate = "44100"  format.channels = "1"  format.channel_map = "\"mono\""
	Поток данных приостановлен: нет
	Звук выключен: нет
	Громкость: mono: 65530 / 100% / -0,00 dB
	        баланс 0,00
	Задержка буфера: 0 мкс
	Задержка аудиоприёмника: 3237 мкс
	Метод передискретизации: speex-float-1
	Свойства:
		media.name = "playback"
		application.name = "speech-dispatcher-dummy"
		native-protocol.peer = "UNIX socket client"
		native-protocol.version = "35"
		application.process.id = "4232"
		application.process.user = "pcuser"
		application.process.host = "pcuser-desktop"
		application.process.binary = "sd_dummy"
		application.language = "C"
		window.x11.display = ":0"
		application.process.machine_id = "211d6c3d504645f1ae2d1a729f952ce3"
		module-stream-restore.id = "sink-input-by-application-name:speech-dispatcher-dummy"
pcuser@pcuser-desktop:~$ pactl get-default-sink
alsa_output.pci-0000_00_1f.3.analog-stereo
pcuser@pcuser-desktop:~$ pactl list short sinks
0	alsa_output.pci-0000_00_1f.3.analog-stereo	module-alsa-card.c	s16le 2-канальный 4800	RUNNING
1	sipbot_sink	module-null-sink.c	s16le 2-канальный 4800	IDLE
 

