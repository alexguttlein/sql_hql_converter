import os
import tempfile

import streamlit as st

from converters.hql_to_sql import HqlToSqlConverter
from converters.sql_to_hql import SqlToHqlConverter
from core.metadata import HibernateMetadata

st.set_page_config(page_title="Conversor HQL ↔ Oracle SQL", layout="wide")
st.title("Conversor HQL (Java) ↔ Oracle SQL")


@st.cache_resource
def load_metadata(xml_bytes: bytes) -> HibernateMetadata:
    meta = HibernateMetadata()
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        f.write(xml_bytes)
        path = f.name
    try:
        meta.load_from_file(path)
    finally:
        os.remove(path)
    return meta


xml_file = st.file_uploader("hibernateConsole.hbm.xml", type=["xml"])
if not xml_file:
    st.info("Suba el archivo hibernateConsole.hbm.xml para continuar.")
    st.stop()

try:
    metadata = load_metadata(xml_file.getvalue())
except Exception as exc:
    st.error(f"No se pudo procesar el XML: {exc}")
    st.stop()

st.caption(f"{len(metadata.entities_by_table)} tablas mapeadas")

mode = st.radio(
    "Tipo de conversión",
    ["HQL (Java StringBuilder) → Oracle SQL", "Oracle SQL → HQL (Java StringBuilder)"],
    horizontal=True,
)
query = st.text_area("Pegue aquí su consulta", height=250)

if st.button("CONVERTIR", type="primary"):
    if not query.strip():
        st.warning("Pegue una consulta antes de convertir.")
    else:
        conv_cls = HqlToSqlConverter if mode.startswith("HQL") else SqlToHqlConverter
        try:
            result, notes = conv_cls(metadata).convert(query)
            st.code(result, language="sql" if mode.startswith("HQL") else "java")
            for n in notes:
                st.caption(n)
        except Exception as exc:
            st.error(str(exc))