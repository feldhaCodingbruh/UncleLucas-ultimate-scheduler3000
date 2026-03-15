from flask import Flask, render_template, request, redirect, url_for
import calendar
import re

app = Flask(__name__)

workers = []
generated_schedule = []

schedule_settings = {
    "month": 3,
    "year": 2026,
    "weekday_shifts": [
        {"label": "Pamaina 1", "start": "10:00", "end": "18:30"},
        {"label": "Pamaina 2", "start": "13:00", "end": "21:30"},
    ],
    "weekend_shifts": [
        {"label": "Pamaina 1", "start": "10:00", "end": "18:30"},
        {"label": "Pamaina 2", "start": "13:00", "end": "21:30"},
        {"label": "Pamaina 3", "start": "15:00", "end": "21:30"},
    ]
}


def parse_availability_lines(raw_text):
    return [line.strip() for line in raw_text.splitlines() if line.strip()]


def get_days_in_month(year, month):
    return calendar.monthrange(year, month)[1]


def get_worker_status(day_count, expected_days):
    if day_count == expected_days:
        return "Gerai"
    elif day_count < expected_days:
        return f"Trūksta {expected_days - day_count} d."
    return f"Per daug: +{day_count - expected_days} d."


def normalize_text(text):
    text = text.strip().lower()

    replacements = {
        "ė": "e",
        "ū": "u",
        "ų": "u",
        "į": "i",
        "š": "s",
        "ž": "z",
        "ą": "a",
        "č": "c",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def extract_time(text):
    match = re.search(r'(\d{1,2})(?::(\d{2}))?', text)

    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0

    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"

    return None


def time_to_minutes(time_str):
    hour, minute = map(int, time_str.split(":"))
    return hour * 60 + minute


def minutes_to_time(minutes):
    hour = minutes // 60
    minute = minutes % 60
    return f"{hour:02d}:{minute:02d}"


def shift_length_hours(start, end):
    start_m = time_to_minutes(start)
    end_m = time_to_minutes(end)
    return (end_m - start_m) / 60


def can_cover_shift(available_start, available_end, shift_start, shift_end):
    return available_start <= shift_start and available_end >= shift_end


def etatas_to_month_hours(etatas):
    # paprastas pirmas variantas
    # 1.0 etatas ≈ 160 val./mėn.
    return float(etatas) * 160


def parse_single_line(line):
    original = line.strip()
    normalized = normalize_text(original)

    result = {
        "original": original,
        "type": "unknown",
        "from_time": None,
        "until_time": None,
        "second_from_time": None,
        "preference": None,
        "parsed_text": "Neatpažinta"
    }

    morning_words = [
        "rytas",
        "rytine",
        "rytines",
        "geriau rytas",
        "noriu rytines"
    ]

    if any(word in normalized for word in morning_words):
        result["preference"] = "Rytinė pageidaujama"

    if "negaliu" in normalized:
        result["type"] = "unavailable"
        result["parsed_text"] = "Negali dirbti"
        return result

    if "iki" in normalized and "nuo" in normalized and "/" in normalized:
        parts = normalized.split("/")

        until_time = None
        from_time = None

        for part in parts:
            part = part.strip()

            if "iki" in part:
                until_time = extract_time(part)

            if "nuo" in part:
                from_time = extract_time(part)

        result["type"] = "split"
        result["until_time"] = until_time
        result["second_from_time"] = from_time

        if until_time and from_time:
            result["parsed_text"] = f"Gali iki {until_time} ir nuo {from_time}"
        else:
            result["parsed_text"] = "Padalintas laikas"

        return result

    if "nuo" in normalized:
        time_value = extract_time(normalized)

        result["type"] = "from_time"
        result["from_time"] = time_value
        result["parsed_text"] = f"Gali nuo {time_value}" if time_value else "Gali nuo tam tikro laiko"
        return result

    if "iki" in normalized:
        time_value = extract_time(normalized)

        result["type"] = "until_time"
        result["until_time"] = time_value
        result["parsed_text"] = f"Gali iki {time_value}" if time_value else "Gali iki tam tikro laiko"
        return result

    if "galiu" in normalized:
        result["type"] = "available"
        result["parsed_text"] = "Gali visą dieną"
        return result

    if any(word in normalized for word in morning_words):
        result["type"] = "available"
        result["parsed_text"] = "Gali visą dieną (pageidauja rytinės)"
        return result

    return result


def check_one_shift_fit(parsed_item, shift_start, shift_end):
    shift_start_min = time_to_minutes(shift_start)
    shift_end_min = time_to_minutes(shift_end)

    result = {
        "ok": False,
        "reason": "Neatpažinta",
        "assigned_start": shift_start,
        "assigned_end": shift_end,
        "is_shortened": False
    }

    def valid_short_shift(start_min, end_min):
        length = end_min - start_min
        return length >= 270  # 4.5 val minimum

    if parsed_item["type"] == "available":
        result["ok"] = True
        result["reason"] = "Gali visą dieną"
        return result

    if parsed_item["type"] == "unavailable":
        result["ok"] = False
        result["reason"] = "Negali dirbti"
        return result

    if parsed_item["type"] == "from_time" and parsed_item["from_time"]:
        available_from = time_to_minutes(parsed_item["from_time"])

        if available_from <= shift_start_min:
            result["ok"] = True
            result["reason"] = f"Gali nuo {parsed_item['from_time']}"
            return result

        if shift_start_min < available_from < shift_end_min:
            if valid_short_shift(available_from, shift_end_min):
                result["ok"] = True
                result["reason"] = f"Sutrumpinta pamaina nuo {parsed_item['from_time']}"
                result["assigned_start"] = minutes_to_time(available_from)
                result["assigned_end"] = shift_end
                result["is_shortened"] = True
                return result

        result["ok"] = False
        result["reason"] = f"Gali nuo {parsed_item['from_time']}"
        return result

    if parsed_item["type"] == "until_time" and parsed_item["until_time"]:
        available_until = time_to_minutes(parsed_item["until_time"])

        if available_until >= shift_end_min:
            result["ok"] = True
            result["reason"] = f"Gali iki {parsed_item['until_time']}"
            return result

        if shift_start_min < available_until < shift_end_min:
            if valid_short_shift(shift_start_min, available_until):
                result["ok"] = True
                result["reason"] = f"Sutrumpinta pamaina iki {parsed_item['until_time']}"
                result["assigned_start"] = shift_start
                result["assigned_end"] = minutes_to_time(available_until)
                result["is_shortened"] = True
                return result

        result["ok"] = False
        result["reason"] = f"Gali iki {parsed_item['until_time']}"
        return result

    if parsed_item["type"] == "split" and parsed_item["until_time"] and parsed_item["second_from_time"]:
        first_end = time_to_minutes(parsed_item["until_time"])
        second_start = time_to_minutes(parsed_item["second_from_time"])

        if shift_end_min <= first_end:
            result["ok"] = True
            result["reason"] = f"Gali iki {parsed_item['until_time']}"
            return result

        if second_start <= shift_start_min:
            result["ok"] = True
            result["reason"] = f"Gali nuo {parsed_item['second_from_time']}"
            return result

        if shift_start_min < first_end < shift_end_min:
            if valid_short_shift(shift_start_min, first_end):
                result["ok"] = True
                result["reason"] = f"Sutrumpinta pamaina iki {parsed_item['until_time']}"
                result["assigned_start"] = shift_start
                result["assigned_end"] = minutes_to_time(first_end)
                result["is_shortened"] = True
                return result

        if shift_start_min < second_start < shift_end_min:
            if valid_short_shift(second_start, shift_end_min):
                result["ok"] = True
                result["reason"] = f"Sutrumpinta pamaina nuo {parsed_item['second_from_time']}"
                result["assigned_start"] = minutes_to_time(second_start)
                result["assigned_end"] = shift_end
                result["is_shortened"] = True
                return result

        result["ok"] = False
        result["reason"] = f"Gali iki {parsed_item['until_time']} ir nuo {parsed_item['second_from_time']}"
        return result

    return result


def build_shift_fit_for_day(parsed_item, shifts):
    fit = []

    for shift in shifts:
        fit_result = check_one_shift_fit(parsed_item, shift["start"], shift["end"])

        fit.append({
            "label": shift["label"],
            "start": shift["start"],
            "end": shift["end"],
            "ok": fit_result["ok"],
            "reason": fit_result["reason"],
            "assigned_start": fit_result["assigned_start"],
            "assigned_end": fit_result["assigned_end"],
            "is_shortened": fit_result["is_shortened"]
        })

    return fit


def get_day_type(year, month, day):
    weekday_index = calendar.weekday(year, month, day)
    # 0=Mon ... 6=Sun
    if weekday_index <= 3:
        return "weekday", weekday_index
    return "weekend", weekday_index


def weekday_name_from_index(index):
    names = ["Pr", "An", "Tr", "Kt", "Pn", "Št", "Sk"]
    return names[index]


def parse_worker_availability(lines, year, month):
    parsed_days = []

    for day, line in enumerate(lines, start=1):
        parsed = parse_single_line(line)
        day_type, weekday_index = get_day_type(year, month, day)
        shifts = schedule_settings["weekday_shifts"] if day_type == "weekday" else schedule_settings["weekend_shifts"]
        shift_fit = build_shift_fit_for_day(parsed, shifts)

        parsed["day"] = day
        parsed["day_type"] = day_type
        parsed["weekday_name"] = weekday_name_from_index(weekday_index)
        parsed["shift_fit"] = shift_fit
        parsed_days.append(parsed)

    return parsed_days


def rebuild_all_workers():
    year = schedule_settings["year"]
    month = schedule_settings["month"]

    for worker in workers:
        worker["parsed_availability"] = parse_worker_availability(
            worker["availability_lines"],
            year,
            month
        )
        worker["day_count"] = len(worker["availability_lines"])


def calculate_targets(valid_workers, total_shift_count):
    total_etatas = sum(float(worker["etatas"]) for worker in valid_workers)
    targets = {}

    if total_etatas == 0:
        for i in range(len(valid_workers)):
            targets[i] = 0
        return targets

    for i, worker in enumerate(valid_workers):
        targets[i] = (float(worker["etatas"]) / total_etatas) * total_shift_count

    return targets


def assignment_time_to_minutes(shift_time):
    start_str, end_str = shift_time.split("-")
    return time_to_minutes(start_str), time_to_minutes(end_str)


def has_gap_between_assignments(assignments):
    usable = [a for a in assignments if a["worker_name"]]

    if len(usable) < 2:
        return False

    usable_sorted = sorted(
        usable,
        key=lambda a: assignment_time_to_minutes(a["shift_time"])[0]
    )

    for i in range(len(usable_sorted) - 1):
        _, current_end = assignment_time_to_minutes(usable_sorted[i]["shift_time"])
        next_start, _ = assignment_time_to_minutes(usable_sorted[i + 1]["shift_time"])

        if current_end < next_start:
            return True

    return False


def sort_workers_for_display(worker_list):
    return sorted(
        worker_list,
        key=lambda w: (-float(w["etatas"]), w["name"].lower())
    )


def generate_month_schedule():
    global generated_schedule

    year = schedule_settings["year"]
    month = schedule_settings["month"]
    expected_days = get_days_in_month(year, month)

    valid_workers = workers[:]
    valid_workers = sort_workers_for_display(valid_workers)

    total_shift_count = 0
    for day in range(1, expected_days + 1):
        day_type, _ = get_day_type(year, month, day)
        if day_type == "weekday":
            total_shift_count += len(schedule_settings["weekday_shifts"])
        else:
            total_shift_count += len(schedule_settings["weekend_shifts"])

    targets = calculate_targets(valid_workers, total_shift_count)
    assigned_counts = {i: 0 for i in range(len(valid_workers))}
    assigned_hours = {i: 0.0 for i in range(len(valid_workers))}
    generated_schedule = []

    for day in range(1, expected_days + 1):
        day_type, weekday_index = get_day_type(year, month, day)
        weekday_name = weekday_name_from_index(weekday_index)
        shifts = schedule_settings["weekday_shifts"] if day_type == "weekday" else schedule_settings["weekend_shifts"]

        day_record = {
            "day": day,
            "weekday_name": weekday_name,
            "day_type": day_type,
            "assignments": [],
            "warnings": []
        }

        assigned_today = set()

        for shift_index, shift in enumerate(shifts):
            candidates = []

            for worker_index, worker in enumerate(valid_workers):
                if worker_index in assigned_today:
                    continue

                if day - 1 >= len(worker["parsed_availability"]):
                    continue

                parsed_day = worker["parsed_availability"][day - 1]
                shift_fit = parsed_day["shift_fit"]

                if shift_index >= len(shift_fit):
                    continue

                fit_info = shift_fit[shift_index]
                if not fit_info["ok"]:
                    continue

                score = assigned_counts[worker_index] - targets[worker_index]

                if shift_index == 0 and parsed_day.get("preference") == "Rytinė pageidaujama":
                    score -= 0.25

                # lengvas prioritetas pilnai pamainai prieš sutrumpintą
                if fit_info["is_shortened"]:
                    score += 0.15

                candidates.append((score, worker_index, worker["name"], fit_info))

            candidates.sort(key=lambda x: x[0])

            if candidates:
                _, chosen_index, chosen_name, chosen_fit = candidates[0]
                assigned_today.add(chosen_index)
                assigned_counts[chosen_index] += 1

                shift_hours = shift_length_hours(
                    chosen_fit["assigned_start"],
                    chosen_fit["assigned_end"]
                )
                assigned_hours[chosen_index] += shift_hours

                day_record["assignments"].append({
                    "shift_label": shift["label"],
                    "shift_time": f"{chosen_fit['assigned_start']}-{chosen_fit['assigned_end']}",
                    "worker_name": chosen_name
                })
            else:
                day_record["assignments"].append({
                    "shift_label": shift["label"],
                    "shift_time": f"{shift['start']}-{shift['end']}",
                    "worker_name": None
                })
                day_record["warnings"].append(
                    f"Nėra darbuotojo {shift['label']} ({shift['start']}-{shift['end']})"
                )

        if has_gap_between_assignments(day_record["assignments"]):
            day_record["warnings"].append("Yra tarpas grafike – parduotuvė liktų tuščia")

        generated_schedule.append(day_record)

    worker_summary = []
    for i, worker in enumerate(valid_workers):
        target_hours = etatas_to_month_hours(worker["etatas"])

        worker_summary.append({
            "name": worker["name"],
            "etatas": worker["etatas"],
            "assigned_shifts": assigned_counts[i],
            "assigned_hours": round(assigned_hours[i], 1),
            "target_hours": round(target_hours, 1),
            "hours_difference": round(assigned_hours[i] - target_hours, 1)
        })

    worker_summary = sorted(
        worker_summary,
        key=lambda w: (-float(w["etatas"]), w["name"].lower())
    )

    return worker_summary


@app.route("/")
def home():
    year = schedule_settings["year"]
    month = schedule_settings["month"]
    expected_days = get_days_in_month(year, month)

    workers_with_status = []
    for worker in workers:
        worker_copy = worker.copy()
        worker_copy["status"] = get_worker_status(worker["day_count"], expected_days)
        workers_with_status.append(worker_copy)

    workers_with_status = sort_workers_for_display(workers_with_status)

    return render_template(
        "index.html",
        workers=workers_with_status,
        selected_month=month,
        selected_year=year,
        expected_days=expected_days,
        settings=schedule_settings,
        generated_schedule=generated_schedule,
        worker_summary=None
    )


@app.route("/save_settings", methods=["POST"])
def save_settings():
    month = request.form.get("month", "").strip()
    year = request.form.get("year", "").strip()

    try:
        month = int(month)
        year = int(year)

        if 1 <= month <= 12 and 2000 <= year <= 2100:
            schedule_settings["month"] = month
            schedule_settings["year"] = year
            rebuild_all_workers()
    except ValueError:
        pass

    return redirect(url_for("home"))


@app.route("/add_worker", methods=["POST"])
def add_worker():
    name = request.form.get("name", "").strip()
    etatas = request.form.get("etatas", "").strip()
    availability_raw = request.form.get("availability", "").strip()

    availability_lines = parse_availability_lines(availability_raw)
    parsed_availability = parse_worker_availability(
        availability_lines,
        schedule_settings["year"],
        schedule_settings["month"]
    )

    if name and etatas:
        workers.append({
            "name": name,
            "etatas": etatas,
            "availability_raw": availability_raw,
            "availability_lines": availability_lines,
            "parsed_availability": parsed_availability,
            "day_count": len(availability_lines)
        })

    return redirect(url_for("home"))


@app.route("/delete_worker/<int:index>", methods=["POST"])
def delete_worker(index):
    sorted_workers = sort_workers_for_display(workers)

    if 0 <= index < len(sorted_workers):
        target_worker = sorted_workers[index]
        for real_index, worker in enumerate(workers):
            if worker is target_worker:
                workers.pop(real_index)
                break

    return redirect(url_for("home"))


@app.route("/generate_schedule", methods=["POST"])
def generate_schedule_route():
    year = schedule_settings["year"]
    month = schedule_settings["month"]
    expected_days = get_days_in_month(year, month)

    workers_with_status = []
    for worker in workers:
        worker_copy = worker.copy()
        worker_copy["status"] = get_worker_status(worker["day_count"], expected_days)
        workers_with_status.append(worker_copy)

    workers_with_status = sort_workers_for_display(workers_with_status)

    worker_summary = generate_month_schedule()

    return render_template(
        "index.html",
        workers=workers_with_status,
        selected_month=month,
        selected_year=year,
        expected_days=expected_days,
        settings=schedule_settings,
        generated_schedule=generated_schedule,
        worker_summary=worker_summary
    )


if __name__ == "__main__":
    app.run(debug=True)
