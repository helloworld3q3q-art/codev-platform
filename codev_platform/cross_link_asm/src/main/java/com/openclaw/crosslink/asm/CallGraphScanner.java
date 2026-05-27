package com.openclaw.crosslink.asm;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.stream.Stream;

/**
 * Scan compiled .class files under a root directory, emit caller/callee method-call edges as JSONL.
 *
 * <p>Only edges where BOTH owner classes start with "com/openclaw/" are emitted (project-internal calls).
 *
 * <p>Usage: {@code java -jar cross-link-asm-1.0.0-shaded.jar <classes-root-dir>}
 *
 * <p>Output (stdout, one JSON object per line):
 * <pre>
 * {"caller_class":"com.openclaw.stock.admin.facade.ConfigurationFacadeService",
 *  "caller_method":"recommendPage",
 *  "callee_class":"com.openclaw.stock.admin.infrastructure.mapper.ConfigurationQueryMapper",
 *  "callee_method":"selectRecommendResults",
 *  "opcode":"INVOKEINTERFACE"}
 * </pre>
 */
public final class CallGraphScanner {

    private static final String INTERNAL_PREFIX = "com/openclaw/";
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private CallGraphScanner() {
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("Usage: java -jar cross-link-asm.jar <classes-root-dir>");
            System.exit(2);
        }
        Path root = Paths.get(args[0]).toAbsolutePath();
        if (!Files.isDirectory(root)) {
            System.err.println("Not a directory: " + root);
            System.exit(2);
        }

        long fileCount = 0;
        long emitCount = 0;
        try (Stream<Path> stream = Files.walk(root)) {
            for (Path p : (Iterable<Path>) stream::iterator) {
                if (!Files.isRegularFile(p)) {
                    continue;
                }
                String name = p.getFileName().toString();
                if (!name.endsWith(".class")) {
                    continue;
                }
                fileCount++;
                emitCount += scanClassFile(p);
            }
        }
        System.err.println("[asm] scanned " + fileCount + " class files, emitted " + emitCount + " call edges");
    }

    private static long scanClassFile(Path classFile) {
        try (InputStream in = Files.newInputStream(classFile)) {
            ClassReader reader = new ClassReader(in);
            String internalOwner = reader.getClassName(); // e.g. com/openclaw/.../Foo
            if (!internalOwner.startsWith(INTERNAL_PREFIX)) {
                return 0L;
            }
            CallEdgeCollector collector = new CallEdgeCollector(internalOwner);
            reader.accept(collector, ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
            return collector.emitted;
        } catch (IOException e) {
            System.err.println("[asm] read failed: " + classFile + " -> " + e.getMessage());
            return 0L;
        }
    }

    /** Visit one class -> for each method body, capture method-call instructions. */
    private static final class CallEdgeCollector extends ClassVisitor {
        private final String callerClassInternal;
        long emitted = 0L;

        CallEdgeCollector(String callerClassInternal) {
            super(Opcodes.ASM9);
            this.callerClassInternal = callerClassInternal;
        }

        @Override
        public MethodVisitor visitMethod(int access, String name, String descriptor,
                                         String signature, String[] exceptions) {
            return new CallSiteVisitor(callerClassInternal, name, this);
        }
    }

    private static final class CallSiteVisitor extends MethodVisitor {
        private final String callerClassInternal;
        private final String callerMethod;
        private final CallEdgeCollector parent;

        CallSiteVisitor(String callerClassInternal, String callerMethod, CallEdgeCollector parent) {
            super(Opcodes.ASM9);
            this.callerClassInternal = callerClassInternal;
            this.callerMethod = callerMethod;
            this.parent = parent;
        }

        @Override
        public void visitMethodInsn(int opcode, String owner, String name,
                                    String descriptor, boolean isInterface) {
            if (owner == null || !owner.startsWith(INTERNAL_PREFIX)) {
                return;
            }
            // Skip self-calls (caller == callee, same method) — uncommon but cheap to ignore.
            if (owner.equals(callerClassInternal) && callerMethod.equals(name)) {
                return;
            }
            // Skip synthetic constructor / static init noise — we only want business method edges.
            if ("<init>".equals(name) || "<clinit>".equals(name)) {
                return;
            }
            if ("<init>".equals(callerMethod) || "<clinit>".equals(callerMethod)) {
                return;
            }

            Map<String, String> row = new LinkedHashMap<>();
            row.put("caller_class", internalToFqn(callerClassInternal));
            row.put("caller_method", callerMethod);
            row.put("callee_class", internalToFqn(owner));
            row.put("callee_method", name);
            row.put("opcode", opcodeName(opcode));
            try {
                System.out.println(MAPPER.writeValueAsString(row));
                parent.emitted++;
            } catch (IOException e) {
                System.err.println("[asm] json serialize failed: " + e.getMessage());
            }
        }

        private static String internalToFqn(String internal) {
            // Strip inner-class $ suffix so SimpleClassName lookup matches the outer class.
            int dollar = internal.indexOf('$');
            String trimmed = dollar >= 0 ? internal.substring(0, dollar) : internal;
            return trimmed.replace('/', '.');
        }

        private static String opcodeName(int opcode) {
            return switch (opcode) {
                case Opcodes.INVOKEVIRTUAL -> "INVOKEVIRTUAL";
                case Opcodes.INVOKESPECIAL -> "INVOKESPECIAL";
                case Opcodes.INVOKESTATIC -> "INVOKESTATIC";
                case Opcodes.INVOKEINTERFACE -> "INVOKEINTERFACE";
                case Opcodes.INVOKEDYNAMIC -> "INVOKEDYNAMIC";
                default -> "OP_" + opcode;
            };
        }
    }
}
