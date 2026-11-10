#!/usr/bin/env python3
"""Автоматическое обновление scripts/lint.py правилами из обзора Питера Камински.

Запуск из корня репозитория DragonSigh/zhivoy-russkiy:
    python3 scripts/update_lint_kaminski.py
"""
from pathlib import Path
import subprocess
import sys


def patch_lint(lint_path: Path) -> None:
    text = lint_path.read_text(encoding="utf-8")

    # 1. Расширение «вводный шум» и добавление «открыточная метафора» и «размытый авторитет» в BUREAUCRACY
    old_noise = (
        '("вводный шум", re.compile(r"\\bбезусловно,\\b|\\bразумеется,\\b|\\bочевидно,\\b|'
        '\\bкак известно\\b|\\bпо сути,\\b|\\bв принципе,\\b|\\bследует отметить\\b|'
        '\\bнеобходимо подчеркнуть\\b|\\bважно отметить\\b|\\bнеобходимо учитывать, что\\b|'
        '\\bсоответственно,\\b|\\bесли (?:коротко|вкратце)\\b|\\bбез лишних слов\\b", re.I), '
        '"выбрось вводное - фраза не теряет ничего"),'
    )
    new_noise = (
        '("вводный шум", re.compile(r"\\bбезусловно,\\b|\\bразумеется,\\b|\\bочевидно,\\b|'
        '\\bкак известно\\b|\\bне секрет, что\\b|\\bни для кого не секрет\\b|\\bпо сути,\\b|'
        '\\bв принципе,\\b|\\bследует отметить\\b|\\bстоит отметить(?:, что)?\\b|'
        '\\bважно понимать, что\\b|\\bследует учитывать, что\\b|\\bнеобходимо подчеркнуть\\b|'
        '\\bважно отметить\\b|\\bнеобходимо учитывать, что\\b|\\bболее того,\\b|\\bкроме того,\\b|'
        '\\bв заключение стоит отметить\\b|\\bподводя итог(?:и)?,\\b|\\bисходя из вышеизложенного\\b|'
        '\\bсоответственно,\\b|\\bесли (?:коротко|вкратце)\\b|\\bбез лишних слов\\b", re.I), '
        '"выбрось вводный мостик или штамп - фраза не теряет ничего"),\n'
        '    ("открыточная метафора", re.compile(r"\\bмаяк\\w* в океане\\b|\\bпутеводн\\w+ звезд\\w+|'
        '\\bсимфони\\w+ вкус\\w*|\\bкалейдоскоп\\w* событи\\w*|\\bкраеугольн\\w+ кам(?:ень|нем|ня)\\b|'
        '\\bдве стороны одной медали\\b|\\bраскрыть потенциал\\b|\\bпогрузиться в мир\\b", re.I), '
        '"выброси открыточный ИИ-штамп и дай конкретную деталь"),\n'
        '    ("размытый авторитет", re.compile(r"\\bэксперты утверждают\\b|'
        '\\bспециалисты сходятся во мнении\\b|\\bмногочисленные исследования показывают\\b|'
        '\\bуч[её]ные доказали, что\\b", re.I), '
        '"назови конкретный источник, автора или цифру вместо ссылки в пустоту"),'
    )
    if "открыточная метафора" not in text:
        if old_noise in text:
            text = text.replace(old_noise, new_noise, 1)
        else:
            raise RuntimeError("Не найден якорь 'вводный шум' в BUREAUCRACY")

    # 2. Расширение правила «является» в B_DENSITY на «представляет собой» и «выступает в качестве/роли»
    old_yavl = (
        '("является", re.compile(r"\\bявля(?:ется|ются|лся|лась|лось|лись)\\b", re.I), 1, '
        '"«Х - метод» вместо «Х является методом»"),'
    )
    new_yavl = (
        '("является", re.compile(r"\\bявля(?:ется|ются|лся|лась|лось|лись)\\b|'
        '\\bпредставля(?:ет|ют|л[аои]?) собой\\b|\\bвыступа(?:ет|ют|л[аои]?) в (?:качестве|роли)\\b", re.I), 1, '
        '"связка-паразит («является», «представляет собой», «выступает в качестве»): замени живым глаголом"),'
    )
    if "представля(?:ет|ют" not in text:
        if old_yavl in text:
            text = text.replace(old_yavl, new_yavl, 1)
        else:
            raise RuntimeError("Не найден якорь 'является' в B_DENSITY")

    # 3. Добавление новых калек из обзора Камински и теста 15 нейросетей в CALQUES
    old_calque_anchor = (
        '("уведомлены", re.compile(r"\\bвы будете уведомлены\\b", re.I), '
        '"«мы пришлём уведомление» - живой деятель вместо пассива"),'
    )
    new_calques = (
        old_calque_anchor + "\n"
        '    ("изменяющее жизнь", re.compile(r"\\bизменяющ\\w+ жизнь\\b|\\bменяющ\\w+ правила игры\\b", re.I), '
        '"калька «life-changing / game-changing»: перестрой через придаточное или следствие"),\n'
        '    ("сердце части", re.compile(r"\\bв сердце (?:материковой|континентальной) части\\b", re.I), '
        '"калька «in the heart of the mainland»: «в глубине континентальной части»"),\n'
        '    ("скопление бэкпекеров", re.compile(r"\\bскоплени\\w+ бэкпекер\\w*|\\bтусовк\\w+ экспат\\w*", re.I), '
        '"калька «backpacker hubs / expat hangouts»: «кварталы для путешественников», «любимые кафе экспатов»"),\n'
        '    ("чашка чая", re.compile(r"\\bне (?:моя|ваша|твоя|его|её|ее|их) чашка чая\\b", re.I), '
        '"буквальный перевод идиомы «not your cup of tea»: «не по душе», «не в моём вкусе»"),'
    )
    if "изменяющее жизнь" not in text:
        if old_calque_anchor in text:
            text = text.replace(old_calque_anchor, new_calques, 1)
        else:
            raise RuntimeError("Не найден якорь 'уведомлены' в CALQUES")

    # 4. Добавление проверки «забор из тире» в категорию M (после bold_count)
    old_bold_block = (
        '    bold_count = len(re.findall(r"\\*\\*[^\\n]+\\*\\*", strip_frontmatter(text)))\n'
        '    if words >= 200 and bold_count > words / 200 + 1:\n'
        '        findings.append(("WARN", "M", "жирный перебор", 0,\n'
        '            f"{bold_count} жирных на {words} слов (норма ~{max(1, words // 200)})",\n'
        '            "выделяй только главное, примерно один спан на 200 слов"))'
    )
    new_bold_and_dash_block = (
        old_bold_block + "\n\n"
        '    # забор из тире: тире в 4+ предложениях подряд или в >=45% предложений (при >=6 предложениях)\n'
        '    if len(sents) >= 6 and not muted("забор из тире", "M"):\n'
        '        dash_flags = [bool(re.search(r"\\s[—–]\\s", s)) for s in sents]\n'
        '        dash_total = sum(dash_flags)\n'
        '        dash_run = max_dash_run = 0\n'
        '        for df in dash_flags:\n'
        '            dash_run = dash_run + 1 if df else 0\n'
        '            max_dash_run = max(max_dash_run, dash_run)\n'
        '        if max_dash_run >= 4 or dash_total / len(sents) >= 0.45:\n'
        '            findings.append(("WARN", "M", "забор из тире", 0,\n'
        '                f"тире в {dash_total} из {len(sents)} предложений (серия подряд: {max_dash_run})",\n'
        '                "убери забор из тире: замени часть тире глаголами, двоеточием или союзами"))'
    )
    if "забор из тире" not in text:
        if old_bold_block in text:
            text = text.replace(old_bold_block, new_bold_and_dash_block, 1)
        else:
            raise RuntimeError("Не найден блок 'жирный перебор' в lint()")

    # 5. Добавление ассертов в self_test() перед проверкой golden-пар
    old_golden_anchor = '    # golden-пары: «плохо» всегда хуже «хорошо»'
    new_self_tests = (
        '    # проверки паттернов из обзора Камински\n'
        '    f, *_ = lint("Стоит отметить, что платформа представляет собой маяк в океане возможностей.")\n'
        '    assert any(x[2] == "вводный шум" for x in f), f\n'
        '    assert any(x[2] == "открыточная метафора" for x in f), f\n'
        '    assert any(x[2] == "является" for x in f), f\n'
        '    f, *_ = lint("Поездка оставляет изменяющее жизнь впечатление, хотя это не моя чашка чая.")\n'
        '    assert any(x[2] == "изменяющее жизнь" for x in f), f\n'
        '    assert any(x[2] == "чашка чая" for x in f), f\n'
        '    f, *_ = lint("Первое — скорость. Второе — точность. Третье — надёжность. Четвёртое — простота. Пятое — цена. Шестое — опыт.")\n'
        '    assert any(x[2] == "забор из тире" for x in f), f\n\n'
        + old_golden_anchor
    )
    if "проверки паттернов из обзора Камински" not in text:
        if old_golden_anchor in text:
            text = text.replace(old_golden_anchor, new_self_tests, 1)
        else:
            raise RuntimeError("Не найден якорь golden-пар в self_test()")

    lint_path.write_text(text, encoding="utf-8")
    print(f"Успешно обновлён файл: {lint_path}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "lint.py"
    if not target.is_file():
        print(f"Файл не найден: {target}", file=sys.stderr)
        sys.exit(2)
    patch_lint(target)
    res = subprocess.run([sys.executable, str(target), "--self-test"])
    sys.exit(res.returncode)
