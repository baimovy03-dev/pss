# Демо-бот записи клиентов

Python 3.12+, aiogram 3, SQLite. Один специалист, часовые приёмы каждый день
с 09:00 до 18:00. Часовой пояс по умолчанию Asia/Bishkek.
База защищает время от двойной записи, отменять можно только свои записи.

## Локальный запуск

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
export BOT_TOKEN='токен, полученный у @BotFather'
export BOT_TIMEZONE=Asia/Bishkek
.venv/bin/python -m booking_bot
```

Токен храните только в окружении или защищённом файле вне Git. Не отправляйте
его в чат. DATABASE_PATH задаёт путь SQLite (по умолчанию data/bookings.sqlite3).
Один токен должен использовать только один запущенный polling-процесс.
При активном webhook сначала удалите его через Telegram Bot API.

Команды: `/start`, `/book ГГГГ-ММ-ДД ЧЧ:00`, `/mine`, `/cancel НОМЕР`, `/faq`.
Напоминания проверяются каждые 30 секунд и отправляются за час до визита.
При перезапуске восстанавливаются из базы; прошедшие визиты не напоминаются.
Если процесс остановился после отправки, но до записи результата, возможно
повторное напоминание. Это демо без платежей, админ-панели и выбора услуг.
Перед реальным использованием настройте FAQ, расписание и правила хранения данных.

## Проверка

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Тесты не требуют Telegram и проверяют SQLite, конфликты времени, отмену,
сохранение записей и очередь напоминаний. Для живой проверки запустите бота,
в Telegram создайте будущую запись, проверьте `/mine`, попытайтесь занять то же
время другим пользователем, затем отмените запись. Для напоминания выберите
ближайший рабочий час. Нужен HTTPS-доступ к api.telegram.org.

## systemd на вашем Linux-сервере

Скопируйте проект в `/opt/booking-bot`, затем от root:

```sh
useradd --system --home /opt/booking-bot --shell /usr/sbin/nologin bookingbot
python3 -m venv /opt/booking-bot/.venv
/opt/booking-bot/.venv/bin/pip install -r /opt/booking-bot/requirements.lock
install -m 600 /dev/null /etc/booking-bot.env
```

Если пользователь уже существует, пропустите useradd. Отредактируйте
`/etc/booking-bot.env` и задайте `BOT_TOKEN=...` и `BOT_TIMEZONE=Asia/Bishkek`
(без export). Не запускайте команду install повторно для существующего файла:
она очищает его. Пользователю bookingbot нужен доступ на чтение проекта и venv.

```sh
cp /opt/booking-bot/deploy/booking-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now booking-bot
systemctl status booking-bot
journalctl -u booking-bot -n 50 --no-pager
```

systemd создаст каталог `/var/lib/booking-bot` для базы. Делайте резервные копии
SQLite через sqlite3 backup API или после остановки службы. Токен не включайте
в резервные копии исходников. systemd-службу проверяйте на целевом сервере.
