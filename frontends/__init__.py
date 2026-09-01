"""
前端层 / Front-end layer.

每个前端（CLI / NiceGUI）都是独立入口，只调用 dna.* 的公开 API，
彼此之间不互相依赖，可以单独替换或新增。
Each front-end is a standalone entry point that only calls the public dna.* API.
Front-ends never depend on one another and can be replaced or added independently.
"""
