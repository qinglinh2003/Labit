from __future__ import annotations


def _load_streamlit():
    import streamlit as st

    return st


def main() -> None:
    st = _load_streamlit()
    st.set_page_config(page_title="LABIT", layout="wide")
    st.title("LABIT")


if __name__ == "__main__":
    main()
