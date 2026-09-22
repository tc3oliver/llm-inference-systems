# 研究後續（postscript）

<!--
已於 2026-09-22 發佈：接在 study.meowcoder.com 的〈償還 reusable state 的債〉
(https://study.meowcoder.com/posts/260921-canonical-state-debt-recovery/) 結論
之後、出處段之前，標題降一級以配合該文的層級。同一次發佈把文末那句「#3793
（draft，寫這篇時還沒送審）」改成它現在的狀態；那是唯一動到的正文。

這份檔案是這段文字在本 repo 的正本：文章與這裡不一致時，以這裡為準。正文其餘
部分不改——正文寫的是實驗，這一段寫的是實驗之後、把機制送上游時發生的事。

原則與這個 repo 一致：
- 不寫成 PR changelog，不列 test 數字，不貼完整記憶體表格。
- 不宣稱任何 PR 已經 merged。
- 不宣稱前景延遲或 admission headroom 有改善。
- 數字一律回連 repo。
-->

文章寫完之後，這個機制被整理成上游的 pull request，過程本身產生了三件文章裡沒有
的東西。

## 機制送上游了，還沒有被合併

背景 canonical state recovery 以
[omlx#3793](https://github.com/jundot/omlx/pull/3793) 送出，目前開放審查、CI 綠、
尚未有維護者審到結論。它相依於下面那個修正，應該排在它後面合併。**開著的 pull
request 是提案，不是成果**，這裡不會把它寫成已經進上游。

## 驗證 PCSR 時掉出一個獨立的正確性缺陷

為了確認補回的 canonical state 真的等同於 dense 讀過一遍的狀態，我必須把 sparse
與 dense 兩條路徑放在一起比。這個比較暴露出一件跟背景補回無關的事：在 mRoPE 的
VLM 上，SpecPrefill 把選中的 token 寫在**壓縮之後**的位置，而不是它們在原始序列裡
的位置。

這是 [omlx#3811](https://github.com/jundot/omlx/pull/3811)。

兩件事要講清楚。**PCSR 沒有造成這個缺陷**——把背景補回整個關掉，它一樣存在；PCSR
提供的只是那個讓它現形的 dense 對照。而修好它之後改善了什麼，也不能多講：位置契約
恢復了，首個 token 的 logit 在六輪裡六輪都更靠近 dense baseline，但在這個 workload
上 greedy 輸出的一致性並沒有因此變好。那是量出來的負面結果，不是還沒量。

## 把它做到 production 品質，逼出三個系統教訓

真正讓我意外的是這一段。把一個在研究 build 上跑得好好的機制整理到可以被審查，又找
出六個缺陷——而實驗自己的 workload 一個都碰不到。它們需要的條件是：同一個 process
裡載了第二個模型、MTP 打開、補回途中遇到 eviction、prompt 長度剛好是 cache block
的整數倍。這些條件在 `data/` 的任何一輪裡都不存在。

所以 `data/` 裡沒有任何一個數字因此改變。這是在講那些跑的覆蓋範圍，不是在替它辯護。

六個裡有三個不只是這個 runtime 的問題。

**背景工作要讓出的是所有權，不只是執行。** 補回的任務停止計算之後，仍然抱著它那份
materialized 的 dense cache——那是實際配置出來的陣列，不是對 paged pool 的參照。排
程器裡每一個推理「誰在用裝置」的原語，都會把這種任務報成閒置。最尖的一個情況是：
記憶體壓力觸發的 throttle 會去回收 buffer pool，而它回收的時候，任務手上還握著自己
擁有的最大一塊配置。

契約是這樣寫的：**已發佈的 canonical state 是耐久的；正在進行、還沒發佈的重建狀態
是可丟棄的。**

這裡要很小心地說清楚量到了什麼。滯留狀態的大小是量出來的，而且對 context 完全線
性；把它放掉之後 MLX 的 active memory 會如數回來，也是量出來的。**前景延遲或
admission headroom 有沒有因此改善，沒有建立。** process 的實體 footprint 在
allocator 收斂之後根本不動，而且同樣的工作跑兩次，基線本身的變動就比想找的效果大。
這個修正是靠所有權的契約成立的，不是靠一個量到的前景改善。

**前景的優先權需要在執行之前就可見。** 排程器手上的儀器——decode registry、prefill
tracker——回答的都是「有沒有東西正在跑」。背景任務需要的是「有沒有東西正在等」，而
這兩個問題之間的那段區間，正好就是讓路還有意義的區間。到達的可見性必須是跨 process
的，必須在執行之前存在，而且必須從到達一直維持到請求離開，不是到 admission 為止。

**Cache 的 watermark 不是 cache 的事實。** `committed_tokens` 是記帳，只會往上走；
它所宣稱的那些 block 卻可以被 evict 掉。兩邊各自移動，而只有在它們被比較的那一瞬間
，前者才是後者的證據。任何一個要把 watermark 帶過時間的元件，都必須能讓它往回走。

這三條的完整版本、各自的不變式與回歸測試，在 repo 的
[`HARDENING.md`](https://github.com/tc3oliver/llm-inference-systems/blob/main/experiments/exp-003-progressive-shadow-prefill/HARDENING.md)；
記憶體那一組的六個量測點、收斂控制組，以及哪些是 derived、哪些是 not established，
在
[`data/recovery-foreground-qos/`](https://github.com/tc3oliver/llm-inference-systems/tree/main/data/recovery-foreground-qos)。

## EXP-003 現在的狀態

研究結果完成；上游驗證未結束。問題回答了，機制建立了，受控與真實 workload 都跑
了，限制也寫下來了。剩下的是維護者的審查，以及 #3811 和 #3793 各自的上游結果——那
不是我這邊能收掉的部分。
