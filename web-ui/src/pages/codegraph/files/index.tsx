// CodeGraph 文件树浏览器主页面。
//
// 左侧 Antd Tree 渲染已索引的项目文件层级；右侧选中文件后通过 'contains' 边
// 列出该文件包含的代码节点（class / method / function / ...）。
//
// 数据源：common/services 的 fetchFileTree / fetchNode / fetchNeighbors。
// codegraph 当前没有"按 file_path 列出所有节点"接口，所以右侧依赖文件节点
// (id=file:<path>) 的 'contains' 出边来还原其内部节点列表。

import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Skeleton,
  Space,
  Spin,
  Typography,
} from 'antd';
import { useModel } from '@umijs/max';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { fetchFileTree } from '@/pages/codegraph/common/services';
import type { FileDTO } from '@/pages/codegraph/common/types';

import FileNodesPanel from './components/FileNodesPanel';
import FileTree from './components/FileTree';
import {
  buildFileTree,
  collectAncestorKeysForMatches,
  collectDirKeys,
  collectTopTwoLayerDirKeys,
  type FileTreeNodeData,
} from './utils';

const FilesPage: React.FC = () => {
  // 订阅当前项目, 切项目后文件树原地重拉(fetch 注入 X-Project-Id)。
  const { currentProjectId } = useModel('project');
  const [files, setFiles] = useState<FileDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | undefined>(undefined);
  // 用户是否手动操作过展开树（手动后不再被搜索自动覆盖）
  const [userExpanded, setUserExpanded] = useState(false);

  const loadFiles = useCallback(async (): Promise<void> => {
    setLoading(true);
    setLoadFailed(false);
    try {
      const data = await fetchFileTree();
      const items = data?.items ?? [];
      setFiles(items);
      // 默认展开顶层 2 层（apps / python / docs 等大目录第二层）
      const tree = buildFileTree(items);
      setExpandedKeys(collectTopTwoLayerDirKeys(tree));
      setUserExpanded(false);
    } catch {
      setFiles([]);
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
    // currentProjectId 变化触发重拉
  }, [currentProjectId]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  // 树数据（按 keyword 模糊过滤叶子文件 path）
  const treeData: FileTreeNodeData[] = useMemo(() => {
    const lower = keyword.trim().toLowerCase();
    const filtered = lower
      ? files.filter((f) => ((f.path as string) ?? '').toLowerCase().includes(lower))
      : files;
    return buildFileTree(filtered);
  }, [files, keyword]);

  // keyword 变化时自动展开命中路径（仅在用户未手动调整树展开状态时）
  useEffect(() => {
    if (userExpanded) {
      return;
    }
    if (!keyword.trim()) {
      // 关键字清空 -> 回到默认顶 2 层展开
      setExpandedKeys(collectTopTwoLayerDirKeys(treeData));
      return;
    }
    const ancestors = collectAncestorKeysForMatches(treeData, keyword);
    setExpandedKeys(ancestors);
  }, [keyword, treeData, userExpanded]);

  const fileFallback = useMemo<FileDTO | undefined>(() => {
    if (!selectedPath) {
      return undefined;
    }
    return files.find((f) => f.path === selectedPath);
  }, [files, selectedPath]);

  const handleKeywordChange = useCallback((e: React.ChangeEvent<HTMLInputElement>): void => {
    setKeyword(e.target.value);
  }, []);

  const handleExpand = useCallback((keys: React.Key[]): void => {
    setExpandedKeys(keys.map((k) => String(k)));
    setUserExpanded(true);
  }, []);

  const handleSelect = useCallback((path: string | undefined): void => {
    setSelectedPath(path);
  }, []);

  const handleExpandAll = useCallback((): void => {
    setExpandedKeys(collectDirKeys(treeData));
    setUserExpanded(true);
  }, [treeData]);

  const handleCollapseAll = useCallback((): void => {
    setExpandedKeys([]);
    setUserExpanded(true);
  }, []);

  const handleRefresh = useCallback((): void => {
    loadFiles();
  }, [loadFiles]);

  const totalFiles = files.length;
  const filteredFiles = useMemo(() => {
    if (!keyword.trim()) {
      return files;
    }
    const lower = keyword.toLowerCase();
    return files.filter((f) => ((f.path as string) ?? '').toLowerCase().includes(lower));
  }, [files, keyword]);

  const selectedKeys = useMemo(() => (selectedPath ? [selectedPath] : []), [selectedPath]);

  return (
    <div className="p-16">
      <Card classNames={{ root: 'i:mb-16' }}>
        <Space orientation="vertical" className="w-full" size={12}>
          <Space className="w-full">
            <Input
              prefix={<SearchOutlined />}
              placeholder="按路径过滤，如 apps/codegraph-api/"
              value={keyword}
              onChange={handleKeywordChange}
              allowClear
              style={{ width: 360 }}
            />
            <Button onClick={handleExpandAll}>全部展开</Button>
            <Button onClick={handleCollapseAll}>全部收起</Button>
            <Button icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading}>
              刷新
            </Button>
            <Typography.Text type="secondary" className="text-12">
              已索引 {totalFiles} 个文件
              {keyword.trim() ? ` · 命中 ${filteredFiles.length}` : ''}
            </Typography.Text>
          </Space>
          {loadFailed ? (
            <Alert
              type="error"
              title="加载文件列表失败"
              description="请确认 codegraph 服务已启动，且已索引项目代码图谱（.codegraph/codegraph.db）。"
              showIcon
            />
          ) : null}
        </Space>
      </Card>

      <Row gutter={16}>
        <Col span={10}>
          <Card classNames={{ root: 'i:h-full' }} title="文件树" styles={{ body: { padding: 12 } }}>
            {loading ? (
              <Skeleton active paragraph={{ rows: 12 }} />
            ) : treeData.length === 0 ? (
              <Empty description={keyword ? '无匹配的文件' : '暂无已索引文件'} />
            ) : (
              <Spin spinning={loading}>
                <div style={{ maxHeight: 'calc(100vh - 260px)', overflow: 'auto' }}>
                  <FileTree
                    treeData={treeData}
                    expandedKeys={expandedKeys}
                    selectedKeys={selectedKeys}
                    onExpand={handleExpand}
                    onSelect={handleSelect}
                  />
                </div>
              </Spin>
            )}
          </Card>
        </Col>
        <Col span={14}>
          <FileNodesPanel filePath={selectedPath} fileFallback={fileFallback} />
        </Col>
      </Row>
    </div>
  );
};

export default FilesPage;
