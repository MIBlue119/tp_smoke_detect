# Intent

```
我們有一個計劃是要幫政府機關做一個AI影像抽菸行為偵測系統
- 透過非中國的AI模型，在地端算力的狀況下 
- 近即時的支援20x個鏡頭，來監測幾個區域內是否有人在偷抽煙
- 若有偵測到時會播放預錄好的『請不要抽煙』的語音提醒
- 而這個專案會有被議員質詢的壓力 因此他的false alarm希望能降低，要避免當使用者挖鼻孔,嚼檳榔等被誤判為抽菸 同時也可能鏡頭無法拍到太遠的內容 
- The service is the ai core, we would focus on ai api

#context
- Currently we are at 202608
- vlm 在想可否用liquid,gemma,mistral系列等，或是有其他適合的
- 算力:目前我們預想的算力約莫是5090或是L40s 等級 當然要更好也可以評估
```



## Tech Stack

- python
- uv to manage dependency
- docker container
- pydantic manage settings
- deepstream(if it is ok to support)
- pytest
- linting



## Others

- Please follow python's style guide and zen
- You could stop other occupied gpu container to try your implementations
- Please use system design pattern to make the code base easy to maintain
- I have done some survey
  - /mnt/HDD2/proj_walnutek/tp_smoke_detect/docs/吸菸偵測方案研究

