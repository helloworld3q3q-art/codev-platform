// 简化的JSON显示组件
import { parseJsonString } from '@/utils';
import { Card, Typography } from 'antd';
import React from 'react';

interface JsonDisplayProps {
  data: string;
  title?: string;
}

const Title: React.FC<{ title: string }> = ({ title }) => (
  <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 10 }}>{title}</div>
);

const JsonDisplay: React.FC<JsonDisplayProps> = ({ data, title }) => (
  <Card style={{ marginTop: title ? 10 : 0 }}>
    {title && <Title title={title} />}
    <Typography.Paragraph>
      <pre
        style={{
          background: '#f9fafb',
          borderColor: '#f3f4f6',
          borderRadius: 6,
          padding: 16,
          fontSize: 14,
          color: '#222',
          margin: 0,
          overflow: 'auto',
        }}
      >
        {parseJsonString(data || '')}
      </pre>
    </Typography.Paragraph>
  </Card>
);

export default JsonDisplay;
