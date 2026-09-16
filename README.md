运行指令：

Anaconda Prompt:

cd /d C:\...\lean-playground

pip install -r app\requirements.txt

streamlit run app\app.py


只跑演示模式、调LLM：约几十KB
做真实验证：从Mathlib官方缓存服务器拉预编译产物，约1-2GB，不用自己编译几小时）.lake/也会涨到几个GB

！！拉Mathlib预编译缓存时，按lake-manifest.json锁定的版本拉取 Mathlib 源码（不要用lake update，会把mathlib更新到最新master，和大家的版本不一致）
