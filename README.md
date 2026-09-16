运行指令：
Anaconda Prompt:
cd /d C:\Users\31869\Desktop\Programs\AIcourse\lean-playground
pip install -r app\requirements.txt
streamlit run app\app.py

PowerShell:
C:\Users\31869\anaconda3\Scripts\activate.bat
cd C:\Users\31869\Desktop\Programs\AIcourse\lean-playground
pip install -r app\requirements.txt
streamlit run app\app.py

只跑演示模式、调LLM：约几十KB
做真实验证：从Mathlib官方缓存服务器拉预编译产物，约1-2GB，不用自己编译几小时）.lake/也会涨到几个GB
