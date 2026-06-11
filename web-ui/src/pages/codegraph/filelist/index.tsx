// CodeGraph 文件列表（表格视图）— 进页即调 postFileTree 加载全量已索引文件。
// 与 /codegraph/files 文件树视图互补：filelist 是扁平表格，支持排序 + 路径关键字 + 语言筛选。
// codegraph-api file-tree 不需要关键字，prefix 为可选过滤。

import PageContainer from '@/components/PageContainer';
import ResizableTable from '@/components/ResizableTable';
import { useModel } from '@umijs/max';
import { Alert, Button, Card, Col, Input, Row, Select, Space } from 'antd';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { postFileTree } from '@/services/apis/graphapi';

import type { FileDTO } from '../common/types';
import { LANG_OPTIONS } from '../common/utils';
import { createColumns } from './components/Columns';

const CodeGraphFileListPage: React.FC = () => {
  // 订阅当前项目, 切项目后文件列表原地重拉(fetch 注入 X-Project-Id)。
  const { currentProjectId } = useModel('project');
  const [prefix, setPrefix] = useState('');
  const [languages, setLanguages] = useState<string[]>([]);
  const [items, setItems] = useState<FileDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const loadList = useCallback(async (): Promise<void> => {
    setLoading(true);
    setLoadFailed(false);
    try {
      const res = await postFileTree({ prefix: prefix.trim() || undefined });
      setItems(res.data?.items ?? []);
    } catch {
      setItems([]);
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
    // currentProjectId 变化触发重拉
  }, [prefix, currentProjectId]);

  const handlePrefixChange = useCallback((e: React.ChangeEvent<HTMLInputElement>): void => {
    setPrefix(e.target.value);
  }, []);

  const handleLanguagesChange = useCallback((v: string[]): void => {
    setLanguages(v);
  }, []);

  const handleSearch = useCallback((): void => {
    loadList();
  }, [loadList]);

  const handleReset = useCallback((): void => {
    setPrefix('');
    setLanguages([]);
  }, []);

  const handleRefresh = useCallback((): void => {
    loadList();
  }, [loadList]);

  const columns = useMemo(() => createColumns(), []);

  // 语言筛选纯客户端做（file-tree 接口不接受 language 参数）
  const filteredItems = useMemo(() => {
    if (!languages.length) return items;
    const set = new Set(languages);
    return items.filter((f) => f.language && set.has(f.language));
  }, [items, languages]);

  const rowKey = useCallback(
    (record: FileDTO): string => record.path ?? Math.random().toString(36),
    [],
  );

  // 进页即加载全量（file-tree 默认全量；prefix/语言为本地二次筛）
  useEffect(() => {
    loadList();
  }, [loadList]);

  return (
    <PageContainer>
      <Card classNames={{ root: 'i:mb-16' }} title="过滤条件">
        <Row gutter={12}>
          <Col span={10}>
            <Input
              placeholder="路径前缀（可选），如 codev_platform/"
              value={prefix}
              onChange={handlePrefixChange}
              onPressEnter={handleSearch}
              allowClear
            />
          </Col>
          <Col span={8}>
            <Select
              mode="multiple"
              placeholder="语言筛选（可多选，本地过滤）"
              value={languages}
              onChange={handleLanguagesChange}
              options={LANG_OPTIONS}
              className="w-full"
              maxTagCount="responsive"
              allowClear
            />
          </Col>
          <Col span={6}>
            <Space>
              <Button type="primary" onClick={handleSearch} loading={loading}>
                查询
              </Button>
              <Button onClick={handleRefresh} loading={loading}>
                刷新
              </Button>
              <Button onClick={handleReset}>重置</Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {loadFailed ? (
        <Alert
          type="error"
          showIcon
          title="加载文件列表失败"
          description="请确认后端 codegraph 服务已就绪，且已索引项目代码图谱（.codegraph/codegraph.db）。"
          className="i:mb-16"
        />
      ) : (
        <Alert
          type="info"
          showIcon
          title="CodeGraph 文件列表"
          description={`已索引 ${items.length} 个文件${languages.length ? ` · 当前筛选 ${filteredItems.length}` : ''}。点击行操作可跳转到图视图或文件树视图。`}
          className="i:mb-16"
        />
      )}

      <ResizableTable<FileDTO>
        rowKey={rowKey}
        columns={columns}
        dataSource={filteredItems}
        loading={loading}
        search={false}
        toolBarRender={false}
        options={false}
        pagination={{
          defaultPageSize: 50,
          showSizeChanger: true,
          pageSizeOptions: [20, 50, 100, 200],
        }}
        scroll={{ x: 1100 }}
        locale={{ emptyText: loading ? '加载中…' : '无匹配文件' }}
      />
    </PageContainer>
  );
};

export default CodeGraphFileListPage;
