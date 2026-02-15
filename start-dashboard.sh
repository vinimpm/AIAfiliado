#!/bin/bash
exec streamlit run dashboard/main.py --server.port="${PORT:-8080}" --server.address=0.0.0.0
