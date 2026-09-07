// 左侧文件树：Antd Tree 渲染层级化的 FileDTO；叶子节点显示文件名 + 语言 Tag + nodeCount。

import { FileOutlined, FolderOutlined } from '@ant-design/icons';
import { Tag, Tree, Typography } from 'antd';
import React, { useCallback } from 'react';

import { langColorOf } from '@/pages/codegraph/common/utils';

import { type FileTreeNodeData } from '../utils';

interface FileTreeProps {
  treeData: FileTreeNodeData[];
  expandedKeys: string[];
  selectedKeys: string[];
  onExpand: (keys: React.Key[]) => void;
  onSelect: (path: string | undefined) => void;
}

const renderTitle = (node: FileTreeNodeData): React.ReactNode => {
  if (!node.isLeaf) {
    return (
      <span className="inline-flex items-center gap-4">
        <FolderOutlined className="text-#1677ff" />
        <span>{node.title}</span>
      </span>
    );
  }
  const file = node.file;
  const language = file?.language as string | undefined;
  return (
    <span className="inline-flex items-center gap-4">
      <FileOutlined className="text-#8c8c8c" />
      <span>{node.title}</span>
      {language ? (
        <Tag
          className="i:m-0 text-10"
          style={{ color: langColorOf(language), borderColor: langColorOf(language) }}
        >
          {language}
        </Tag>
      ) : null}
      <Typography.Text type="secondary" className="text-12">
        ({(file?.nodeCount as number | undefined) ?? 0})
      </Typography.Text>
    </span>
  );
};

const FileTree: React.FC<FileTreeProps> = ({
  treeData,
  expandedKeys,
  selectedKeys,
  onExpand,
  onSelect,
}) => {
  const handleSelect = useCallback(
    (keys: React.Key[], info: { node: FileTreeNodeData }): void => {
      const node = info.node;
      const path = node.file?.path as string | undefined;
      if (node.isLeaf && path) {
        onSelect(path);
      } else {
        onSelect(undefined);
      }
    },
    [onSelect],
  );

  return (
    <Tree
      treeData={treeData}
      expandedKeys={expandedKeys}
      selectedKeys={selectedKeys}
      onExpand={onExpand}
      onSelect={handleSelect}
      titleRender={renderTitle}
      showLine={{ showLeafIcon: false }}
      blockNode
    />
  );
};

export default FileTree;
