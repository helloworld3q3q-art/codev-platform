import { showSuccess } from '@/components/Modal';
import { downloadStreamFileSimple } from '@/utils';
import { downloadFile, uploadFile } from '@/utils/fetch/fetch';
import { DownloadOutlined, InboxOutlined } from '@ant-design/icons';
import { Button, message, Modal, Upload } from 'antd';
import type { UploadFile, UploadProps } from 'antd/es/upload/interface';
import React, { useCallback, useState } from 'react';
import styles from './index.less';

interface UploadModalProps {
  open: boolean;
  title?: string;
  templateUrl?: string;
  uploadUrl: string;
  showSuccessModal?: boolean;
  onCancel: () => void;
  onSuccess?: (response: any) => void;
}

const { Dragger } = Upload;

const UploadModal: React.FC<UploadModalProps> = ({
  open,
  title = '导入',
  templateUrl,
  uploadUrl,
  showSuccessModal = true,
  onCancel,
  onSuccess,
}) => {
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);

  // 下载模板
  const handleDownloadTemplate = useCallback(() => {
    if (templateUrl) {
      downloadFile({ url: templateUrl });
    }
  }, [templateUrl]);
  const showSuccessModalTips = (data: any) => {
    if (showSuccessModal) {
      showSuccess({
        title: '导入结果',
        content: (
          <div>
            <p>
              {'总共数量'}：{data.totalnum || data.successnum + data.errornum || 0} {'条'}
            </p>
            <p>
              {'成功数量'}：{data.successnum || 0} {'条'}
            </p>
            <p>
              {'失败数量'}：{data.errornum || 0} {'条'}
            </p>
            {data.errornum > 0 && (
              <p>
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    downloadStreamFileSimple(data.data, data.contentDisposition);
                  }}
                >
                  {'下载错误文件'}
                </a>
              </p>
            )}
          </div>
        ),
      });
    }
  };
  // 自定义上传
  const customRequest = useCallback(
    async (options: any) => {
      const { file, onSuccess: onUploadSuccess, onError } = options;

      setUploading(true);
      try {
        const response = await uploadFile({
          url: uploadUrl,
          file,
          responseType: 'blob',
          onProgress: (percent) => {
            const newFileList = [...fileList];
            const targetFile = newFileList.find((item) => item.uid === file.uid);
            if (targetFile) {
              targetFile.percent = percent;
              setFileList(newFileList);
            }
          },
        });
        setUploading(false);
        onUploadSuccess(response, file);

        if (onSuccess) {
          onSuccess(response);
        }
        if (showSuccessModal) {
          showSuccessModalTips(response);
        }
        onCancel();
        // 清空文件列表
        setFileList([]);
      } catch (error) {
        setUploading(false);
        message.error('导入失败');
        onError(error);
      }
    },
    [uploadUrl, fileList, onSuccess, showSuccessModal, onCancel],
  );

  const uploadProps: UploadProps = {
    name: 'file',
    multiple: false,
    fileList,
    beforeUpload: (file) => {
      // 检查文件类型，这里假设只接受 Excel 文件
      const isExcel =
        file.type === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' ||
        file.type === 'application/vnd.ms-excel';
      if (!isExcel) {
        message.error('只能上传 Excel 文件！');
        return Upload.LIST_IGNORE;
      }
      return false; // 返回 false 阻止自动上传
    },
    onChange: ({ fileList: newFileList }) => {
      setFileList(newFileList);
    },
    customRequest,
    onRemove: () => {
      setFileList([]);
      return true;
    },
  };
  const handleCancel = useCallback(() => {
    setFileList([]);
    if (onCancel) {
      onCancel();
    }
  }, [onCancel]);

  const handleSubmit = useCallback(() => {
    // 触发上传：取列表第一个文件交给 customRequest
    if (fileList.length > 0 && fileList[0].originFileObj) {
      customRequest({
        file: fileList[0].originFileObj,
        onSuccess: () => {},
        onError: () => {},
      });
    }
  }, [fileList, customRequest]);

  return (
    <Modal
      size={700}
      title={title}
      open={open}
      onCancel={handleCancel}
      footer={[
        <Button key="cancel" onClick={onCancel}>
          {'取消'}
        </Button>,
        <Button
          key="submit"
          type="primary"
          loading={uploading}
          disabled={fileList.length === 0}
          onClick={handleSubmit}
        >
          {'保存'}
        </Button>,
      ]}
    >
      <div className={styles['import-modal-content']}>
        <div className="flex flex-between mb-20">
          <div>{'请按照模板格式填写并导入文件'}</div>
          {templateUrl && (
            <div className="template-download">
              <Button icon={<DownloadOutlined />} onClick={handleDownloadTemplate}>
                {'下载模板'}
              </Button>
            </div>
          )}
        </div>

        <Dragger {...uploadProps}>
          <p>
            <InboxOutlined />
          </p>
          <p>{'单击或拖动文件到此区域上传'}</p>
        </Dragger>
      </div>
    </Modal>
  );
};

export default UploadModal;
