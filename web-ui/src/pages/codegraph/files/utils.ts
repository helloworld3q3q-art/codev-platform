// 文件树浏览器工具函数：扁平 path 数组 -> Antd Tree 层级结构 / 字节数格式化。
// kind / 语言配色统一复用 common/utils（nodeColorOf / langColorOf，返回 hex，用 style 注入）。

import type { DataNode } from 'antd/es/tree';

import type { FileDTO } from '@/pages/codegraph/common/types';

export interface FileTreeNodeData extends DataNode {
  key: string;
  title: string;
  isLeaf: boolean;
  // 仅叶子节点（文件）携带的元信息
  file?: FileDTO;
  children?: FileTreeNodeData[];
}

// 字节数 -> 易读字符串（B / KB / MB）
export const formatBytes = (bytes?: number): string => {
  if (bytes === null || bytes === undefined || bytes < 0) {
    return '-';
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    const kb = bytes / 1024;
    return `${kb.toFixed(1)} KB`;
  }
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(2)} MB`;
};

// 把扁平的 FileDTO 列表按 path 层级转成 Antd Tree DataNode。
//
// 输入：
//   [
//     { path: 'apps/codegraph-api/src/X.java', ... },
//     { path: 'apps/codegraph-api/src/Y.java', ... },
//   ]
//
// 输出：
//   [
//     { key: 'apps', title: 'apps', isLeaf: false, children: [
//       { key: 'apps/codegraph-api', title: 'codegraph-api', isLeaf: false, children: [
//         { key: 'apps/codegraph-api/src', title: 'src', isLeaf: false, children: [
//           { key: 'apps/codegraph-api/src/X.java', title: 'X.java', isLeaf: true, file: {...} },
//           { key: 'apps/codegraph-api/src/Y.java', title: 'Y.java', isLeaf: true, file: {...} },
//         ]},
//       ]},
//     ]},
//   ]
export const buildFileTree = (files: FileDTO[]): FileTreeNodeData[] => {
  // 内部 map：key=完整 path，value=节点；children 用 Map 维持插入顺序便于排序
  const root: Map<string, FileTreeNodeData> = new Map();
  // 临时索引：key -> childrenMap 加速查找
  const childrenIndex: Map<string, Map<string, FileTreeNodeData>> = new Map();
  childrenIndex.set('', root);

  const ensureDir = (parentKey: string, segment: string, fullKey: string): FileTreeNodeData => {
    const parentMap = childrenIndex.get(parentKey);
    if (!parentMap) {
      // 理论不可达
      const newMap = new Map<string, FileTreeNodeData>();
      childrenIndex.set(parentKey, newMap);
      return ensureDir(parentKey, segment, fullKey);
    }
    const existing = parentMap.get(segment);
    if (existing) {
      return existing;
    }
    const node: FileTreeNodeData = {
      key: fullKey,
      title: segment,
      isLeaf: false,
      children: [],
    };
    parentMap.set(segment, node);
    childrenIndex.set(fullKey, new Map());
    return node;
  };

  for (const file of files) {
    const path = (file.path as string) ?? '';
    if (!path) {
      continue;
    }
    const segments = path.split('/').filter((s) => s.length > 0);
    if (segments.length === 0) {
      continue;
    }

    let parentKey = '';
    for (let i = 0; i < segments.length - 1; i += 1) {
      const segment = segments[i];
      const fullKey = parentKey ? `${parentKey}/${segment}` : segment;
      ensureDir(parentKey, segment, fullKey);
      parentKey = fullKey;
    }

    // 叶子文件
    const fileSegment = segments[segments.length - 1];
    const parentMap = childrenIndex.get(parentKey);
    if (parentMap && !parentMap.has(fileSegment)) {
      const leaf: FileTreeNodeData = {
        key: path,
        title: fileSegment,
        isLeaf: true,
        file,
      };
      parentMap.set(fileSegment, leaf);
    }
  }

  // 把 Map 递归还原为 children 数组，目录优先 + 名称升序
  const materialize = (map: Map<string, FileTreeNodeData>): FileTreeNodeData[] => {
    const arr = Array.from(map.values());
    arr.sort((a, b) => {
      if (a.isLeaf !== b.isLeaf) {
        return a.isLeaf ? 1 : -1;
      }
      return a.title.localeCompare(b.title);
    });
    for (const node of arr) {
      if (!node.isLeaf) {
        const childMap = childrenIndex.get(node.key);
        node.children = childMap ? materialize(childMap) : [];
      }
    }
    return arr;
  };

  return materialize(root);
};

// 收集顶层两层的目录 key，作为默认展开集合（避免一上来全展开把屏幕塞满）
export const collectTopTwoLayerDirKeys = (nodes: FileTreeNodeData[]): string[] => {
  const result: string[] = [];
  for (const top of nodes) {
    if (top.isLeaf) {
      continue;
    }
    result.push(top.key);
    const children = top.children ?? [];
    for (const second of children) {
      if (!second.isLeaf) {
        result.push(second.key);
      }
    }
  }
  return result;
};

// 收集树中所有非叶子节点的 key（用于"全部展开"）
export const collectDirKeys = (nodes: FileTreeNodeData[]): string[] => {
  const result: string[] = [];
  const visit = (list: FileTreeNodeData[]): void => {
    for (const node of list) {
      if (!node.isLeaf) {
        result.push(node.key);
        if (node.children) {
          visit(node.children);
        }
      }
    }
  };
  visit(nodes);
  return result;
};

// 收集匹配关键字的叶子文件的所有祖先 key（用于搜索后自动展开命中路径）
export const collectAncestorKeysForMatches = (
  nodes: FileTreeNodeData[],
  keyword: string,
): string[] => {
  if (!keyword) {
    return [];
  }
  const lower = keyword.toLowerCase();
  const ancestors: Set<string> = new Set();
  const visit = (list: FileTreeNodeData[], parents: string[]): boolean => {
    let anyHit = false;
    for (const node of list) {
      if (node.isLeaf) {
        if (node.key.toLowerCase().includes(lower)) {
          for (const p of parents) {
            ancestors.add(p);
          }
          anyHit = true;
        }
        continue;
      }
      const childHit = node.children ? visit(node.children, [...parents, node.key]) : false;
      if (childHit) {
        anyHit = true;
      }
    }
    return anyHit;
  };
  visit(nodes, []);
  return Array.from(ancestors);
};
