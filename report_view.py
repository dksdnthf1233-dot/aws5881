"""통계 리포트 화면: 기간을 골라 작업 테마별·위반 유형별 통계와 종합 의견을 보여 준다."""

from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import records as rec
from insights import THEME_COLORS, build_insight_pdf, compute, in_period, summary, to_csv, when
from records import THEMES

AXIS_TEXT, GRID = "#cbd5e1", "#1f2b45"


def esc(text: str) -> str:
    return html.escape(text or "")


def _html(markup: str) -> None:
    st.markdown(f'<div class="sv-wrap">{markup}</div>', unsafe_allow_html=True)


def _style(chart: alt.Chart) -> alt.Chart:
    """어두운 화면에 맞춘 그래프 공통 모양."""
    return (
        chart.configure(background="transparent", font="sans-serif")
        .configure_view(strokeWidth=0)
        .configure_axis(labelColor=AXIS_TEXT, titleColor=AXIS_TEXT, gridColor=GRID, domainColor=GRID,
                        tickColor=GRID, labelFontSize=12, titleFontSize=12, labelLimit=170)
        .configure_legend(labelColor=AXIS_TEXT, titleColor=AXIS_TEXT, orient="bottom", labelFontSize=12,
                          titleFontSize=12, symbolType="square", columns=2, labelLimit=150)
    )


_SCALE = alt.Scale(domain=THEMES, range=[THEME_COLORS[t] for t in THEMES])


# --------------------------------------------------------------------------
# 기록 불러오기·저장
# --------------------------------------------------------------------------
def load_records(store, refresh: bool = False) -> list[dict]:
    if refresh or "records_cache" not in st.session_state:
        st.session_state["records_cache"] = store.load()
    return st.session_state["records_cache"]


def save_record(store, record: dict | None) -> str | None:
    """판독 결과 한 건을 저장한다. 실패하면 사유 문구를 돌려준다(판독 화면은 그대로 쓸 수 있게)."""
    if record is None:
        return None
    try:
        latest = store.load()  # 다른 기기에서 저장한 기록을 덮어쓰지 않도록 최신본에 합친다
        updated = rec.upsert(latest, record)
        store.save_all(updated)
        st.session_state["records_cache"] = updated
        return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)


# --------------------------------------------------------------------------
# 화면
# --------------------------------------------------------------------------
def _pick_period(records: list[dict]) -> tuple[date, date]:
    today = datetime.now(rec_tz()).date()
    mode = st.segmented_control("기간", ["월별", "기간 지정"], default="월별", key="ins_mode",
                                label_visibility="collapsed")
    if mode == "기간 지정":
        picked = st.date_input("분석 기간", value=(today - timedelta(days=29), today), key="ins_range",
                               format="YYYY.MM.DD")
        if isinstance(picked, (list, tuple)):
            if len(picked) == 2:
                return picked[0], picked[1]
            if len(picked) == 1:
                return picked[0], picked[0]
        return today, today
    months = sorted({r["checked_at"][:7] for r in records if r.get("checked_at")} | {today.strftime("%Y-%m")},
                    reverse=True)
    month = st.selectbox("월", months, key="ins_month", format_func=lambda m: f"{m[:4]}년 {int(m[5:])}월")
    start = date(int(month[:4]), int(month[5:]), 1)
    end = (date(start.year + start.month // 12, start.month % 12 + 1, 1) - timedelta(days=1))
    return start, end


def rec_tz():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Asia/Seoul")


def _theme_cards(stats) -> None:
    parts = []
    for theme in THEMES:
        parts.append(
            f'<div class="t" style="--c:{THEME_COLORS[theme]}"><div class="n">{esc(theme)}</div>'
            f'<div class="v">{stats.theme_inspections[theme]}<small>건 점검</small></div>'
            f'<div class="s">위반 {stats.theme_violations[theme]}건 · {stats.ratio(theme):.0%}</div></div>'
        )
    _html('<div class="sv-themes">' + "".join(parts) + "</div>")


def _theme_chart(stats) -> None:
    df = pd.DataFrame({
        "작업": THEMES,
        "위반 건수": [stats.theme_violations[t] for t in THEMES],
        "표시": [f"{stats.theme_violations[t]}건 ({stats.ratio(t):.0%})" for t in THEMES],
    })
    base = alt.Chart(df).encode(
        y=alt.Y("작업:N", sort=THEMES, title=None),
        x=alt.X("위반 건수:Q", title=None, axis=alt.Axis(tickMinStep=1, format="d")),
    )
    bars = base.mark_bar(cornerRadiusEnd=5, height=26).encode(
        color=alt.Color("작업:N", scale=_SCALE, legend=None),
        tooltip=["작업", "위반 건수", alt.Tooltip("표시:N", title="비율")],
    )
    labels = base.mark_text(align="left", dx=6, color="#f1f5f9", fontWeight="bold", fontSize=12).encode(text="표시:N")
    top = max(stats.theme_violations.values()) or 1
    chart = (bars + labels).properties(height=200).encode(
        x=alt.X("위반 건수:Q", title=None, scale=alt.Scale(domain=[0, top * 1.45]),
                axis=alt.Axis(tickMinStep=1, format="d"))
    )
    st.altair_chart(_style(chart), width="stretch", theme=None)


def _type_chart(stats, theme_filter: str) -> None:
    rows = []
    for t in stats.types:
        for theme, count in t.themes.items():
            if theme_filter in ("전체", theme):
                rows.append({"위반 사항": t.name, "작업": theme, "건수": count})
    if not rows:
        st.caption("이 작업에서 확인된 위반 사항이 없습니다.")
        return
    df = pd.DataFrame(rows)
    totals = df.groupby("위반 사항")["건수"].sum().sort_values(ascending=False)
    order = list(totals.index[:10])
    df = df[df["위반 사항"].isin(order)]
    whole = sum(totals)
    labels_df = pd.DataFrame({"위반 사항": order, "합계": [int(totals[n]) for n in order],
                              "표시": [f"{int(totals[n])}건 ({totals[n] / whole:.0%})" for n in order]})
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4, height=20).encode(
        y=alt.Y("위반 사항:N", sort=order, title=None),
        x=alt.X("sum(건수):Q", title=None, stack="zero"),
        color=alt.Color("작업:N", scale=_SCALE, title="작업"),
        order=alt.Order("작업:N"),
        tooltip=["위반 사항", "작업", "건수"],
    )
    labels = alt.Chart(labels_df).mark_text(align="left", dx=6, color="#f1f5f9", fontWeight="bold", fontSize=12).encode(
        y=alt.Y("위반 사항:N", sort=order), x="합계:Q", text="표시:N",
    )
    top = int(totals.iloc[0]) or 1
    chart = (bars + labels).properties(height=max(120, 34 * len(order) + 40)).encode(
        x=alt.X("sum(건수):Q", title=None, scale=alt.Scale(domain=[0, top * 1.5]),
                axis=alt.Axis(tickMinStep=1, format="d"))
    )
    st.altair_chart(_style(chart), width="stretch", theme=None)


def _monthly_chart(stats) -> None:
    rows = [{"월": m.replace("-", "."), "작업": t, "건수": c}
            for m, counts in stats.monthly.items() for t, c in counts.items() if c]
    if not rows:
        return
    chart = alt.Chart(pd.DataFrame(rows)).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("월:N", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("sum(건수):Q", title=None, axis=alt.Axis(tickMinStep=1, format="d")),
        color=alt.Color("작업:N", scale=_SCALE, title="작업"),
        order=alt.Order("작업:N"),
        tooltip=["월", "작업", "건수"],
    ).properties(height=240)
    st.altair_chart(_style(chart), width="stretch", theme=None)


def _summary_card(stats) -> None:
    items = "".join(
        f'<div class="row"><div class="k">{esc(title)}</div><div class="b">{esc(body)}</div></div>'
        for title, body in summary(stats)
    )
    _html(f'<div class="sv-summary">{items}</div>')


def _manage(store, records: list[dict]) -> None:
    with st.expander("🗂 기록 관리 (백업·불러오기·삭제)"):
        st.caption(f"저장 위치: {store.where} · 전체 {len(records)}건")
        stamp = datetime.now(rec_tz()).strftime("%Y%m%d")
        st.download_button("⬇️ 전체 기록 백업 받기 (JSON)", data=json.dumps(records, ensure_ascii=False, indent=1),
                           file_name=f"inspection_records_{stamp}.json", mime="application/json",
                           width="stretch", on_click="ignore", key="ins_backup")
        restore = st.file_uploader("백업 파일 불러오기", type=["json"], key="restore_upload",
                                   label_visibility="collapsed")
        if restore is not None and st.button("불러온 백업을 기록에 합치기", width="stretch", key="ins_restore"):
            try:
                incoming = json.loads(restore.getvalue().decode("utf-8"))
                if not isinstance(incoming, list):
                    raise ValueError("기록 목록 형식이 아닙니다")
                merged = rec.merge(store.load(), incoming)
                store.save_all(merged)
                st.session_state["records_cache"] = merged
                st.success(f"백업을 합쳤습니다. 전체 {len(merged)}건")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"백업을 불러오지 못했습니다: {exc}")

        if records:
            labels = {
                r["id"]: f"{when(r)} · {', '.join(r.get('themes', []))} · "
                         f"위반 {len(r.get('findings', []))}건"
                for r in reversed(records)
            }
            doomed = st.multiselect("삭제할 기록 (시험용 판독 등)", list(labels), format_func=labels.get,
                                    key="ins_delete_pick", placeholder="기록 선택")
            if doomed and st.button(f"선택한 {len(doomed)}건 삭제", width="stretch", key="ins_delete"):
                try:
                    kept = [r for r in store.load() if r.get("id") not in set(doomed)]
                    store.save_all(kept)
                    st.session_state["records_cache"] = kept
                    st.session_state.pop("ins_delete_pick", None)
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"삭제하지 못했습니다: {exc}")


def render(store) -> None:
    _html('<div class="sv-sectitle">📊 안전수칙 위반 통계 리포트</div>')
    if not store.durable:
        st.warning(
            "기록이 앱 서버에 임시 저장되고 있습니다. Streamlit Cloud는 앱이 재시작되면 이 기록을 지웁니다. "
            "오래 보관하려면 secrets에 GITHUB_TOKEN과 GITHUB_REPO를 등록하거나, 아래 '기록 관리'에서 백업을 받아 두세요."
        )
    refresh = st.button("🔄 최신 기록 불러오기", key="ins_refresh", width="stretch")
    try:
        records = load_records(store, refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        st.error(f"기록을 불러오지 못했습니다: {exc}")
        return

    start, end = _pick_period(records)
    if start > end:
        start, end = end, start
    stats = compute(in_period(records, start, end), start, end)
    st.caption(f"분석 기간 {start:%Y.%m.%d} ~ {end:%Y.%m.%d}")

    if not stats.inspections:
        st.info("이 기간에 저장된 점검 기록이 없습니다. 사진을 판독하면 결과가 자동으로 쌓입니다.")
        _manage(store, records)
        return

    _html('<div class="sv-subtitle">1. 작업별 점검 건수</div>')
    _theme_cards(stats)
    _html('<div class="sv-subtitle">2. 작업별 위반 건수·비율</div>')
    _theme_chart(stats)
    _html('<div class="sv-subtitle">3. 많이 발생한 위반 사항</div>')
    theme_filter = st.segmented_control("작업 선택", ["전체", *THEMES], default="전체", key="ins_theme",
                                        label_visibility="collapsed") or "전체"
    _type_chart(stats, theme_filter)
    if len(stats.monthly) > 1:
        _html('<div class="sv-subtitle">월별 위반 추이</div>')
        _monthly_chart(stats)
    _html('<div class="sv-subtitle">4. 종합 의견</div>')
    _summary_card(stats)

    stamp = f"{start:%Y%m%d}_{end:%Y%m%d}"
    try:
        pdf = build_insight_pdf(stats)
        st.download_button("⬇️ 교육용 리포트 PDF 받기", data=pdf, file_name=f"safety_stats_{stamp}.pdf",
                           mime="application/pdf", type="primary", width="stretch", on_click="ignore",
                           key="ins_pdf")
    except Exception as exc:  # noqa: BLE001
        st.error(f"리포트 PDF를 만들지 못했습니다: {exc}")
    st.download_button("⬇️ 위반 목록 엑셀(CSV) 받기", data=to_csv(in_period(records, start, end)),
                       file_name=f"safety_violations_{stamp}.csv", mime="text/csv", width="stretch",
                       on_click="ignore", key="ins_csv")
    _manage(store, records)
