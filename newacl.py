from typing import List


class Node:
    def __init__(self):
        self.son = {}
        self.str = "" # 如果是尾节点，存储对应的单词

class Solution:
    def findWords(self,board:List[list[str]],words:list[str])->List[str]:
        def insert(word:str):
            node = self.root
            for ch in word:
                if ch not in node.son:
                    node.son[ch] = Node()
                node = node.son[ch]
            node.str = word
        #深搜 + 前缀树 深搜就是每个格子走然后加进去单词形成一个word在前缀树里找
        def dfs_Search(board:List[list[str]],i:int,j:int,node:"Node",len_:int):
            if len_ > 10:
                return
            ch = board[i][j]
            if ch not in node.son:
                return #当前字符不存在 即构造的字符串不在words中
            last = node
            node = node.son[ch] #如果有这个节点
            if node.str:
                res.append(node.str)
                node.str = ""
            if not node.son:
                #不是任何单词的前缀，直接把当前的词汇弹出来
                last.son.pop(ch)
            len_ += 1
            board[i][j] = '*' #这里标记一下
            if i-1 >= 0 and board[i-1][j] != '*':
                dfs_Search(board,i-1,j,node,len_)
            if i+1 < len(board) and board[i+1][j] != '*':
                dfs_Search(board,i+1,j,node,len_)
            if j-1>=0 and board[i][j-1] != '*':
                dfs_Search(board,i,j-1,node,len_)
            if j+1 < len(board[0]) and board[i][j+1] != '*':
                dfs_Search(board,i,j+1,node,len_)
            board[i][j] = ch
        m,n = len(board),len(board[0])
        self.root = Node()
        for word in words:
            if len(word)>m*n:
                continue
            insert(word)
        res = []
        for i,row in enumerate(board):
            for j,ch in enumerate(row):
                dfs_Search(board,i,j,self.root,0) #回溯函数 从每个位置找
        return res
    def letterCombinations(self,digits:str)->List[str]:
        if not digits:
            return []
        phone_map = {
            "2": "abc",
            "3": "def",
            "4": "ghi",
            "5": "jkl",
            "6": "mno",
            "7": "pqrs",
            "8": "tuv",
            "9": "wxyz",
        }
        result = []
        def backtrack(index:int,current_combination:str):
            if index == len(digits):
                result.append(current_combination)
                return
            current_digit = digits[index]
            #获取字母
            letters = phone_map[current_digit]
            for letter in letters:
                backtrack(index+1,current_combination+letter)
            backtrack(0,"")
            return result
    def combine(self,n:int,k:int)->List[List[int]]:
        result = []
        path = []
        def backtrace(index:int):
            if len(path) == k: #这里只有每个path的长度是这个
                result.append(path.copy())
                return
            for i in range(index,n+1):
                path.append(i)
                backtrace(i+1)
                path.pop()
            backtrace(1)
            return result

    def permute(self,nums:List[int])->List[List[int]]:
        res = []
        path = []
        #啥叫全排列，就是所有存在的数字
        used = [False]*len(nums)
        def backtrace():
            if len(path)==len(nums):
                res.append(path.copy())
                return
            for i in range(len(nums)):
                if used[i]:
                    continue
                path.append(nums[i])
                used[i] = True
                backtrace()
                path.pop()
                used[i] = False
        backtrace()
        return res
    def combinationSum(self,candidates:List[int],target:int)->List[List[int]]:
        ans = []
        path = []
        #这里传剩下的内容
        def backtrace(index:int,left:int):
            if left==0:
                ans.append(path.copy())
                return
            if index == len(candidates) or left<0:
                return #已经结束了
            backtrace(index+1,left)#不选这个 #我发现了，只要是可以重复选的，或者说是涉及选还是不选的，其实都可以这样子来的 一个回溯是不选 一个回溯是选 只有不选是明确要挪动到下一个索引的
            path.append(candidates[index])
            backtrace(index,left-candidates[index])
            path.pop()
        backtrace(0,target)
        return ans

    def totalNQueens(self,n:int)->int:
        ans = 0
        col = [False] * n
        diag1 = [False]*(2*n-1)
        diag2 = [False]*(2*n-1)

        def dfs(r:int)->None:
            nonlocal ans
            if r==n:
                ans +=1
                return
            for c in range(n):
                d1 = r+c
                d2 = r-c+n-1  #注意相减还是有负数的问题的
                if not col[c] and not diag1[d1] and not diag2[d2]:
                    col[c] = True
                    diag1[d1] = True
                    diag2[d2] = True
                    dfs(r+1)
                    col[c] = diag1[d1] = diag2[d2] = False
        dfs(0)
        return ans










