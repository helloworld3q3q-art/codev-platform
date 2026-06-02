import { PermissionButton } from '@/components/Button';
import { PlusOutlined } from '@ant-design/icons';

interface ToolBarRenderProps {
  onAdd: () => void;
}

const ToolBarRender = ({ onAdd }: ToolBarRenderProps) => [
  <PermissionButton key="new" type="primary" icon={<PlusOutlined />} onClick={onAdd}>
    新增项目
  </PermissionButton>,
];

export default ToolBarRender;
